"""Compile effective security from the shared observed-document declarations."""

from __future__ import annotations

from typing import TYPE_CHECKING

from datamodel_code_generator._api_types import Diagnostic
from datamodel_code_generator._client.naming import token
from datamodel_code_generator._generation_contract import LiteralMapping, LiteralScalar, LiteralSequence
from datamodel_code_generator._runtime.client.scopes import scope_tuple
from datamodel_code_generator._runtime.client.security import (
    SecurityBinding,
    SecurityRequirement,
    SecurityScheme,
    UnavailableSecurityScheme,
)

if TYPE_CHECKING:
    from datamodel_code_generator._generation_contract import (
        FrozenLiteral,
        GeneratedTypeContractBatch,
        OperationContract,
        SourceDocumentId,
        WireDeclaration,
    )
    from datamodel_code_generator._runtime.client.security import SecuritySchemeEntry


def _facts(declaration: WireDeclaration) -> dict[str, object]:
    return {name: value.value for name, value in declaration.facts if isinstance(value, LiteralScalar)}


def _key_scheme(name: str, facts: dict[str, object]) -> SecuritySchemeEntry:
    location, wire_name = facts.get("in"), facts.get("name")
    if location not in {"header", "query", "cookie"} or not isinstance(wire_name, str) or not wire_name:
        return UnavailableSecurityScheme(name=name)
    if location == "query":
        return SecurityScheme(name=name, kind="api_key", location="query", wire_name=wire_name)
    if not token(wire_name):
        return UnavailableSecurityScheme(name=name)
    return SecurityScheme(
        name=name, kind="api_key", location="header" if location == "header" else "cookie", wire_name=wire_name
    )


def _scheme(declaration: WireDeclaration) -> SecuritySchemeEntry:
    name = declaration.name
    assert name is not None
    unavailable = UnavailableSecurityScheme(name=name)
    if any(reference.state != "resolved" for reference in declaration.references):
        return unavailable
    facts = _facts(declaration)
    kind = facts.get("type")
    if kind == "apiKey":
        return _key_scheme(name, facts)
    scheme = facts.get("scheme")
    if kind == "http" and isinstance(scheme, str) and scheme.lower() in {"basic", "bearer"}:
        return SecurityScheme(
            name=name,
            kind="basic" if scheme.lower() == "basic" else "bearer",
            location="header",
            wire_name="Authorization",
        )
    if kind in {"oauth2", "openIdConnect"}:
        return SecurityScheme(name=name, kind="bearer", location="header", wire_name="Authorization")
    return unavailable


def _document(operation: OperationContract) -> SourceDocumentId:
    return operation.declaration.location.document if operation.security_declared else operation.id.use_site.document


class SecurityPlanner:
    """Resolve every requirement in its registration namespace without loading another source."""

    def __init__(self, batch: GeneratedTypeContractBatch, problems: list[Diagnostic]) -> None:
        """Compile complete document catalogues, retaining unusable names as unavailable entries."""
        self.catalogues: dict[SourceDocumentId, dict[str, SecuritySchemeEntry]] = {}
        for item in batch.security_schemes:
            scheme = _scheme(item)
            self.catalogues.setdefault(item.use_site.document, {})[scheme.name] = scheme
        self.root = tuple(self.catalogues.get(batch.documents[0].id, {}).values())
        self.problems = problems

    def problem(self, operation: OperationContract, message: str, *, conflict: bool = False) -> None:
        """Keep a deterministic diagnostic on the operation that requires invalid security."""
        self.problems.append(
            Diagnostic(
                code="E_CONFIG_CONFLICT" if conflict else "E_METADATA_REQUIRED",
                severity="error",
                stage="target",
                message=f"The security of {operation.method.upper()} {operation.path} {message}",
                source_pointer=operation.id.use_site.pointer,
            )
        )

    def binding(self, operation: OperationContract) -> SecurityBinding | None:
        """Preserve absent, empty, and ordered AND/OR requirements in their effective namespace."""
        value = next((value for name, value in operation.facts if name == "security"), None)
        if value is None:
            return None
        schemes = self.catalogues.get(_document(operation), {})
        if not isinstance(value, LiteralSequence):
            self.problem(operation, "must be an array of requirement objects")
            return None
        alternatives: list[tuple[SecurityRequirement, ...]] = []
        for alternative in value.items:
            if not isinstance(alternative, LiteralMapping):
                self.problem(operation, "must contain only requirement objects")
                continue
            requirements: list[SecurityRequirement] = []
            owners: set[tuple[str, str]] = set()
            for key, scopes in alternative.entries:
                name = key.value if isinstance(key, LiteralScalar) else None
                scheme = schemes.get(name) if isinstance(name, str) else None
                if not isinstance(scheme, SecurityScheme):
                    self.problem(operation, f"requires the unavailable scheme {name!r}")
                    continue
                try:
                    required = scope_tuple(
                        tuple(item.value if isinstance(item, LiteralScalar) else None for item in scopes.items)
                        if isinstance(scopes, LiteralSequence)
                        else None
                    )
                except ValueError:
                    self.problem(operation, f"requires valid scope tokens for scheme {name!r}")
                    continue
                position = (
                    scheme.location,
                    scheme.wire_name.lower() if scheme.location == "header" else scheme.wire_name,
                )
                if position in owners:
                    self.problem(
                        operation, f"has overlapping {scheme.location} ownership in one AND alternative", conflict=True
                    )
                owners.add(position)
                requirements.append(SecurityRequirement(scheme=scheme, required_scopes=required))
            alternatives.append(tuple(requirements))
        return SecurityBinding(schemes=tuple(schemes.values()), alternatives=tuple(alternatives))


def _mapping(value: FrozenLiteral | None) -> dict[str, FrozenLiteral]:
    if not isinstance(value, LiteralMapping):
        return {}
    return {
        key.value: item for key, item in value.entries if isinstance(key, LiteralScalar) and isinstance(key.value, str)
    }


def _flow_contract(value: FrozenLiteral | None) -> object:
    return tuple(
        (
            name,
            tuple(
                (key, item.value)
                for key, item in _mapping(flow).items()
                if key in {"authorizationUrl", "tokenUrl", "refreshUrl"} and isinstance(item, LiteralScalar)
            ),
            tuple(sorted(_mapping(_mapping(flow).get("scopes")))),
        )
        for name, flow in _mapping(value).items()
    )


def security_contract(
    batch: GeneratedTypeContractBatch,
    operation: OperationContract,
    binding: SecurityBinding | None,
    *,
    challenge_less: bool,
) -> object:
    """Project only effective requirements, wire facts, flow semantics, and the explicit 401 declaration."""
    if binding is None and not challenge_less:
        return None
    declarations = {
        item.name: item for item in batch.security_schemes if item.use_site.document == _document(operation)
    }
    schemes: list[object] = []
    alternatives = (
        None
        if binding is None
        else tuple(
            tuple((item.scheme.name, item.required_scopes) for item in alternative)
            for alternative in binding.alternatives
        )
    )
    for name in dict.fromkeys(name for alternative in alternatives or () for name, _ in alternative):
        declaration = declarations[name]
        facts = _facts(declaration)
        schemes.append((
            name,
            tuple((key, facts[key]) for key in ("type", "in", "name", "scheme", "openIdConnectUrl") if key in facts),
            _flow_contract(dict(declaration.facts).get("flows")),
        ))
    return {"alternatives": alternatives, "schemes": tuple(schemes), "auth_challenge_less_401": challenge_less}
