"""Resolve a client target's helpers against the accepted input, check their argument names, and record them."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from datamodel_code_generator._api_types import APIGenerationError, Diagnostic, OperationRef, SchemaRef
from datamodel_code_generator._client.config import OPTION_PREFIX
from datamodel_code_generator._client.naming import HELPER_ARGUMENTS, helper_classes
from datamodel_code_generator._client.plan import fact
from datamodel_code_generator._target_documents import document_identity, named_document, portable

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping
    from pathlib import Path

    from datamodel_code_generator._api_generation import TargetRequest
    from datamodel_code_generator._api_types import DiagnosticStage
    from datamodel_code_generator._client.plan import ClientPlan
    from datamodel_code_generator._client.protocols import Helper, Link, ProtocolConfiguration
    from datamodel_code_generator._runtime.model_codecs.media import JSONValue
    from datamodel_code_generator._target_contract import OperationContract, OperationId


@dataclass(frozen=True, slots=True, kw_only=True)
class Protocols:
    """A client target's valid helpers, and the operation or document each of their references names."""

    helpers: tuple[Helper, ...]
    operations: Mapping[OperationRef, OperationContract]
    documents: Mapping[SchemaRef, str]


def _label(operation: OperationContract) -> str:
    return f"{operation.method.upper()} {operation.path}"


def _problem(
    code: str, stage: DiagnosticStage, at: str, message: str, operation: OperationRef | None = None
) -> Diagnostic:
    return Diagnostic(
        code=code,
        severity="error",
        stage=stage,
        message=message,
        source_pointer=None if operation is None else operation.pointer,
        operation=operation,
        option_path=at,
    )


def _undocumented(at: str, document: str | None) -> Diagnostic:
    return _problem("E_CONFIG_VALUE", "config", f"{at}.document", f"{at}.document {document!r} is not a document name")


def plan_protocols(
    request: TargetRequest, source: Path | ProtocolConfiguration | Mapping[str, object] | None, cwd: Path
) -> Protocols | None:
    """Load the target's helpers and resolve their references, raising every problem with the target's identity.

    Every helper, enabled or not, needs each operation it names among the root path operations.
    """
    if source is None:
        return None
    from datamodel_code_generator._client.protocols import load_protocols  # noqa: PLC0415

    helpers, base, problems = load_protocols(source, cwd)
    resolver = _Resolver(request, base)
    if not problems:
        for helper in helpers:
            for link in helper.links:
                problems.extend(resolver.link(link))
            for at, schema in helper.schemas:
                problems.extend(resolver.schema(at, schema))
    if problems:
        raise APIGenerationError(tuple(problems), option_prefix=OPTION_PREFIX)
    return Protocols(helpers=helpers, operations=resolver.operations, documents=resolver.documents)


class _Resolver:
    """Resolve helper operation references against the request, remembering the operation of each one."""

    def __init__(self, request: TargetRequest, base: Path) -> None:
        self.request = request
        self.base = base
        self.operations: dict[OperationRef, OperationContract] = {}
        self.documents: dict[SchemaRef, str] = {}

    def identity(self, document: str | None) -> str | None:
        """Return a named document's identity relative to the base; a malformed name raises ValueError."""
        if document is None:
            return None
        if "\x00" in document:
            msg = "A document name must not contain NUL"
            raise ValueError(msg)
        return document_identity(document, self.base)

    def link(self, link: Link) -> Iterator[Diagnostic]:
        """Resolve one operation reference, and check each request target it takes."""
        reference = link.ref
        try:
            document = self.identity(reference.document)
        except ValueError:
            yield _undocumented(link.at, reference.document)
            return
        if (operation := self.request.resolve(OperationRef(pointer=reference.pointer, document=document))) is None:
            named = named_document(reference.document, document)
            message = f"{link.at} {reference.pointer!r}{named} {self.request.unresolved}"
            yield _problem("E_OPERATION_REF", "config", link.at, message, reference)
            return
        self.operations[reference] = operation
        found = OperationRef(pointer=operation.id.use_site.pointer)
        for at, target in link.targets:
            if (message := _target_problem(operation, target)) is not None:
                yield _problem("E_CONFIG_VALUE", "config", at, f"{at}: {message}", found)

    def schema(self, at: str, schema: SchemaRef) -> Iterator[Diagnostic]:
        """Locate a schema reference's document, resolved against the base, among the accepted input's documents."""
        try:
            document = self.identity(schema.document)
        except ValueError:
            yield _undocumented(at, schema.document)
            return
        if (located := self.request.documents.pointer(document, self.base)) is None:
            message = f"{at} names a schema{named_document(schema.document, document)} outside the accepted input"
            yield _problem("E_CONFIG_VALUE", "config", at, message)
            return
        self.documents[schema] = located


def _target_problem(operation: OperationContract, target: Mapping[str, Any]) -> str | None:
    """Return why an operation cannot take a request target: an undeclared parameter, querystring, or body."""
    declared = [(fact(item, "in"), item.name or "") for item in operation.parameters]
    querystrings = {name for location, name in declared if location == "querystring"}
    location, name = target["in"], target.get("name", "")
    if location == "body":
        return None if operation.request_body is not None else f"{_label(operation)} has no request body"
    if location == "querystring":
        return None if name in querystrings else f"{_label(operation)} has no querystring {name!r}"
    if location == "query" and querystrings:
        return f"{_label(operation)} owns a querystring, which a querystring target writes"
    present = any(
        where == location and (item.lower() == name.lower() if location == "header" else item == name)
        for where, item in declared
    )
    return None if present else f"{_label(operation)} has no {location} parameter {name!r}"


def helper_operations(protocols: Protocols | None) -> frozenset[OperationId]:
    """Return the operations an enabled sending helper calls, whose methods also take the helper's arguments."""
    if protocols is None:
        return frozenset()
    return frozenset(
        protocols.operations[helper.links[0].ref].id for helper in protocols.helpers if helper.enabled and helper.links
    )


def helper_problems(
    protocols: Protocols | None, plan: ClientPlan, checked: Mapping[str, list[Diagnostic]]
) -> Iterator[Diagnostic]:
    """Refuse enabled helpers whose entry operation names an argument as the helper's or whose name gives a taken class.

    Only an explicit name can take a helper's argument: the planner names the others apart. Webhook helpers send
    nothing, so neither applies to them. Then report each enabled helper's own problems in
    declaration order; every kind an enabled helper can have is checked, since validation refuses the later kinds.
    """
    if protocols is None:
        return
    enabled = tuple(helper for helper in protocols.helpers if helper.enabled)
    sending = tuple(helper for helper in enabled if helper.links)
    specs = {spec.contract.id: spec for spec in plan.operations}
    for helper in sending:
        spec = specs[protocols.operations[helper.links[0].ref].id]
        names = (*(parameter.python_name for parameter in spec.parameters), *spec.field_names)
        if taken := sorted(HELPER_ARGUMENTS.intersection(names)):
            message = (
                f"The {helper.kind} helper {helper.name!r} reserves the argument{'s' if len(taken) > 1 else ''} "
                f"{', '.join(map(repr, taken))} of "
                f"{_label(spec.contract)}; rename them with the operation's parameter_names or body_field_names in "
                "--client-operations"
            )
            entry = OperationRef(pointer=spec.contract.id.use_site.pointer)
            yield _problem("E_NAME_COLLISION", "target", helper.at, message, entry)
    yield from _class_problems(sending, protocols)
    for helper in enabled:
        yield from checked[helper.name]


def _class_problems(helpers: tuple[Helper, ...], protocols: Protocols) -> Iterator[Diagnostic]:
    """Refuse an enabled helper whose name gives a namespace or helper class name another name already gave."""
    owners: dict[str, tuple[str, ...]] = {}
    for helper in helpers:
        for parts, class_name in helper_classes(helper.name, helper.kind):
            if (owner := owners.setdefault(class_name, parts)) != parts:
                message = (
                    f"The helper name {helper.name!r} gives the class name {class_name!r}, which {'.'.join(owner)!r} "
                    "already gives"
                )
                entry = OperationRef(pointer=protocols.operations[helper.links[0].ref].id.use_site.pointer)
                yield _problem("E_NAME_COLLISION", "target", helper.at, message, entry)
                break


def helper_metadata(protocols: Protocols | None, request: TargetRequest) -> dict[str, JSONValue]:
    """Return each helper's normalized settings, with references as source references.

    Equivalent spellings of a reference, such as an omitted or explicit root document, give equal settings.
    """
    if protocols is None:
        return {}

    def refer(reference: OperationRef | SchemaRef) -> JSONValue:
        if isinstance(reference, SchemaRef):
            return {"document": protocols.documents[reference], "pointer": reference.pointer}
        return request.documents.operation(protocols.operations[reference].id)

    return {helper.name: portable(helper.tree, refer) for helper in protocols.helpers}
