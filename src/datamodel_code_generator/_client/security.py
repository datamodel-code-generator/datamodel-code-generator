"""Compile effective security from the shared observed-document declarations."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Literal, TypeAlias, TypeGuard

from datamodel_code_generator._api_types import Diagnostic
from datamodel_code_generator._client.naming import CLIENT_KEYWORDS, pascal, snake, token
from datamodel_code_generator._runtime.client.security import (
    SecurityBinding,
    SecurityRequirement,
    SecurityScheme,
    UnavailableSecurityScheme,
)
from datamodel_code_generator._target_contract import LiteralMapping, LiteralScalar, LiteralSequence

if TYPE_CHECKING:
    from collections.abc import Iterable

    from datamodel_code_generator._runtime.client.security import SecuritySchemeEntry
    from datamodel_code_generator._target_contract import (
        GeneratedTypeContractBatch,
        OperationContract,
        SourceDocumentId,
        WireDeclaration,
    )


_SCOPE: Final = re.compile(r"[\x21\x23-\x5b\x5d-\x7e]+")
_ARGUMENTS: Final = frozenset({"self", *CLIENT_KEYWORDS})
_REFRESHED: Final = ("authorizationCode", "password", "deviceAuthorization", "implicit")
Flow: TypeAlias = Literal["client_credentials", "refresh_token"]


@dataclass(frozen=True, slots=True, kw_only=True)
class CredentialSpec:
    """A constructor credential of the clients: its argument name, the scheme it authenticates, and its OAuth flows.

    `flows` names the OAuth providers the scheme declares, each with the absolute token URL it declares or None.
    """

    name: str
    scheme: SecurityScheme
    flows: tuple[tuple[Flow, str | None], ...] = ()


def _scope_items(value: object) -> TypeGuard[tuple[object, ...] | list[object]]:
    return isinstance(value, (tuple, list))


def _scope_tuple(value: object) -> tuple[str, ...]:
    """Copy explicit scope collections into sorted, distinct ASCII tokens."""
    if not _scope_items(value):
        msg = "Scopes must be a tuple or list."
        raise ValueError(msg)
    normalized: set[str] = set()
    for item in value:
        if not isinstance(item, str) or not _SCOPE.fullmatch(item):
            msg = "Scopes must contain valid ASCII scope tokens."
            raise ValueError(msg)
        normalized.add(item)
    return tuple(sorted(normalized))


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


def _url(flow: object, *names: str) -> str | None:
    """Return the first absolute HTTP URL a flow object declares under the names, or None."""
    entries = {
        key.value: value.value
        for key, value in getattr(flow, "entries", ())
        if isinstance(key, LiteralScalar) and isinstance(value, LiteralScalar)
    }
    return next(
        (
            url
            for name in names
            if isinstance(url := entries.get(name), str) and url.startswith(("https://", "http://"))
        ),
        None,
    )


def _flows(declaration: WireDeclaration) -> tuple[tuple[Flow, str | None], ...]:
    """Return the OAuth providers an OAuth 2 or OpenID Connect declaration names, with their declared token URLs.

    A client credentials flow requests tokens itself; every other flow, and OpenID Connect, yields token sets a refresh
    token renews, at the refresh URL or else the token URL of the first such flow declaring one.
    """
    facts = dict(declaration.facts)
    kind = getattr(facts.get("type"), "value", None)
    if kind == "openIdConnect":
        return (("refresh_token", None),)
    if kind != "oauth2":
        return ()
    entries = {
        key.value: value for key, value in getattr(facts.get("flows"), "entries", ()) if isinstance(key, LiteralScalar)
    }
    found: list[tuple[Flow, str | None]] = []
    if "clientCredentials" in entries:
        found.append(("client_credentials", _url(entries["clientCredentials"], "tokenUrl")))
    if set(entries) - {"clientCredentials"}:
        urls = (_url(entries[name], "refreshUrl", "tokenUrl") for name in _REFRESHED if name in entries)
        found.append(("refresh_token", next((url for url in urls if url is not None), None)))
    return tuple(found)


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


class SecurityPlanner:
    """Resolve every requirement among the schemes of the document that uses its operation."""

    def __init__(self, batch: GeneratedTypeContractBatch, problems: list[Diagnostic]) -> None:
        """Compile complete document catalogues, retaining unusable names as unavailable entries."""
        self.catalogues: dict[SourceDocumentId, dict[str, SecuritySchemeEntry]] = {}
        self.declarations: dict[str, WireDeclaration] = {}
        root = batch.documents[0].id
        for item in batch.security_schemes:
            scheme = _scheme(item)
            self.catalogues.setdefault(item.use_site.document, {})[scheme.name] = scheme
            if item.use_site.document == root or scheme.name not in self.declarations:
                self.declarations[scheme.name] = item
        self.root = tuple(self.catalogues.get(root, {}).values())
        self.problems = problems

    def credentials(self, bindings: Iterable[SecurityBinding | None]) -> tuple[CredentialSpec, ...]:
        """Name a constructor credential for each scheme an operation requires, in the order operations name them.

        Its argument is the scheme name in snake case, which must be a new name beside the clients' other arguments,
        and the PascalCase prefix of its OAuth provider classes a new one beside the other providers'.
        """
        used = {
            item.scheme.name: item.scheme
            for binding in bindings
            if binding is not None
            for alternative in binding.alternatives
            for item in alternative
        }
        specs: list[CredentialSpec] = []
        taken = set(_ARGUMENTS)
        providers: set[str] = set()
        for name, scheme in used.items():
            declaration = self.declarations[name]
            argument = snake(name)
            flows = _flows(declaration)
            prefix = pascal(argument) if any(url is not None for _, url in flows) else None
            if not argument or argument in taken:
                self._reserved(name, declaration, f"its credential argument {argument!r}")
            elif prefix is not None and prefix in providers:
                self._reserved(name, declaration, f"the prefix {prefix!r} of its OAuth provider classes")
            taken.add(argument)
            if prefix is not None:
                providers.add(prefix)
            specs.append(CredentialSpec(name=argument, scheme=scheme, flows=flows))
        return tuple(specs)

    def _reserved(self, name: str, declaration: WireDeclaration, what: str) -> None:
        self.problems.append(
            Diagnostic(
                code="E_RESERVED_NAME",
                severity="error",
                stage="target",
                message=f"The security scheme {name!r} needs another name than {what}",
                source_pointer=declaration.use_site.pointer,
            )
        )

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
        """Preserve absent, empty, and ordered AND/OR requirements, naming schemes of the using document."""
        value = next((value for name, value in operation.facts if name == "security"), None)
        if value is None:
            return None
        schemes = self.catalogues.get(operation.id.use_site.document, {})
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
                    required = _scope_tuple(
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
