"""Compile effective security from the shared observed-document declarations."""

from __future__ import annotations

from typing import TYPE_CHECKING

from datamodel_code_generator._api_types import Diagnostic
from datamodel_code_generator._client.naming import token
from datamodel_code_generator._runtime.client.scopes import scope_tuple
from datamodel_code_generator._runtime.client.security import (
    SecurityBinding,
    SecurityRequirement,
    SecurityScheme,
    UnavailableSecurityScheme,
)
from datamodel_code_generator._target_contract import LiteralMapping, LiteralScalar, LiteralSequence

if TYPE_CHECKING:
    from datamodel_code_generator._runtime.client.security import SecuritySchemeEntry
    from datamodel_code_generator._target_contract import (
        GeneratedTypeContractBatch,
        OperationContract,
        SourceDocumentId,
        WireDeclaration,
    )


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
