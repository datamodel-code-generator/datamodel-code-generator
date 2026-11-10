"""Compile effective security from the shared observed-document declarations."""

from __future__ import annotations

from dataclasses import dataclass
from itertools import product
from typing import TYPE_CHECKING, Final, Literal, TypeAlias

from datamodel_code_generator._api_types import Diagnostic
from datamodel_code_generator._client.naming import CLIENT_KEYWORDS
from datamodel_code_generator._runtime.client.security import (
    SecurityBinding,
    SecurityRequirement,
    SecurityScheme,
    UnavailableSecurityScheme,
)
from datamodel_code_generator._runtime.model_codecs.parameters import parameter_key
from datamodel_code_generator._target_contract import (
    LiteralScalar,
    credential_place,
    fact,
    http_scheme,
    label,
    operation_literal,
    operation_schemes,
    requirement_alternatives,
    scheme_catalogues,
    scope_problem,
    unresolved_reference,
)
from datamodel_code_generator._target_naming import NameScope
from datamodel_code_generator._target_operations import declared_in, operation_ref
from datamodel_code_generator.python_literal import stable_repr

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Mapping

    from datamodel_code_generator._openapi_features import ClientFeature
    from datamodel_code_generator._runtime.client.security import SecuritySchemeEntry
    from datamodel_code_generator._target_contract import (
        GeneratedTypeContractBatch,
        OperationContract,
        SourceDocumentId,
        WireDeclaration,
    )


_ARGUMENTS: Final = frozenset({"self", *CLIENT_KEYWORDS})
_REFRESHED: Final = ("authorizationCode", "password", "deviceAuthorization", "implicit")
Flow: TypeAlias = Literal["client_credentials", "refresh_token"]


@dataclass(frozen=True, slots=True, kw_only=True)
class CredentialSpec:
    """A constructor credential of the clients: its argument name, the scheme it authenticates, and its OAuth flows.

    `flows` names the OAuth providers the scheme declares, each with the absolute token URL it declares or None, and
    `prefix` starts the names of its provider classes.
    """

    name: str
    scheme: SecurityScheme
    flows: tuple[tuple[Flow, str | None], ...] = ()
    prefix: str = ""


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


def _provided(flows: tuple[tuple[Flow, str | None], ...]) -> bool:
    """Return whether a scheme's flows declare a token URL, which gives it OAuth provider classes."""
    return any(url is not None for _, url in flows)


def _flows(declaration: WireDeclaration) -> tuple[tuple[Flow, str | None], ...]:
    """Return the OAuth providers an OAuth 2 or OpenID Connect declaration names, with their declared token URLs.

    A client credentials flow requests tokens itself; every other flow, and OpenID Connect, yields token sets a refresh
    token renews, at the refresh URL or else the token URL of the first such flow declaring one.
    """
    facts = dict(declaration.facts)
    kind = fact(declaration, "type")
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


def _feature(declaration: WireDeclaration) -> ClientFeature | None:
    """Return the feature an unavailable scheme declaration uses that the client does not send, or None."""
    if unresolved_reference(declaration) is not None:
        return None
    if fact(declaration, "type") == "mutualTLS":
        return "mutual_tls"
    return None if http_scheme(declaration) is None else "http_auth_schemes"


def _scheme(declaration: WireDeclaration) -> SecuritySchemeEntry:
    name = declaration.name
    assert name is not None
    unavailable = UnavailableSecurityScheme(name=name)
    if unresolved_reference(declaration) is not None:
        return unavailable
    if (place := credential_place(declaration)) is None:
        return unavailable
    location, wire_name = place
    kind = (
        "api_key"
        if fact(declaration, "type") == "apiKey"
        else "basic"
        if http_scheme(declaration) == "basic"
        else "bearer"
    )
    return SecurityScheme(name=name, kind=kind, location=location, wire_name=wire_name)


def _shared(owners: Iterable[list[SecurityRequirement]]) -> Iterator[tuple[SecurityRequirement, ...]]:
    """Return the alternatives one AND alternative means, one per choice among schemes of the same wire form.

    A request carries one credential at a position, so schemes of one kind at one position are met by a single value
    valid for all of them: whichever of their credentials is given, holding the scopes of them all.
    """
    groups = [(group, tuple(sorted({scope for item in group for scope in item.required_scopes}))) for group in owners]
    for choice in product(*(group for group, _ in groups)):
        yield tuple(
            SecurityRequirement(scheme=item.scheme, required_scopes=scopes)
            for item, (_, scopes) in zip(choice, groups, strict=True)
        )


class SecurityPlanner:
    """Resolve every requirement among the schemes of the document that uses its operation."""

    def __init__(self, batch: GeneratedTypeContractBatch, problems: list[Diagnostic]) -> None:
        """Compile complete document catalogues, retaining unusable names as unavailable entries."""
        self.catalogues: dict[SourceDocumentId, dict[str, SecuritySchemeEntry]] = {}
        self.declarations: dict[str, WireDeclaration] = {}
        self.declared = scheme_catalogues(batch)
        self.versions = batch.versions
        root = batch.documents[0].id
        for document, schemes in self.declared.items():
            for name, item in schemes.items():
                self.catalogues.setdefault(document, {})[name] = _scheme(item)
                if document == root or name not in self.declarations:
                    self.declarations[name] = item
        self.root = tuple(self.catalogues.get(root, {}).values())
        self.root_document = root
        self.problems = problems
        assert batch.names is not None
        self.names = batch.names

    def credentials(self, bindings: Iterable[SecurityBinding | None]) -> tuple[CredentialSpec, ...]:
        """Name a constructor credential for each scheme an operation requires, in the order operations name them.

        Its argument is the scheme name in snake case, suffixed apart from the clients' other arguments; the PascalCase
        prefix of its OAuth provider classes is suffixed apart from the other providers' as model classes are.
        """
        used = {
            item.scheme.name: item.scheme
            for binding in bindings
            if binding is not None
            for alternative in binding.alternatives
            for item in alternative
        }
        bases = {name: self.names.function(name) for name in used}
        local = {name: declared_in(self.declarations[name].declaration, self.root_document) for name in used}
        arguments = self.names.claim(NameScope(_ARGUMENTS), [(base, local[name], ()) for name, base in bases.items()])
        flows = {name: _flows(self.declarations[name]) for name in used}
        provided = [(name, argument) for name, argument in zip(used, arguments, strict=True) if _provided(flows[name])]
        prefixes = dict(
            zip(
                (name for name, _ in provided),
                self.names.claim(
                    NameScope(),
                    [(self.names.pascal(argument), local[name], ()) for name, argument in provided],
                    camel=True,
                ),
                strict=True,
            )
        )
        return tuple(
            CredentialSpec(name=argument, scheme=scheme, flows=flows[name], prefix=prefixes.get(name, ""))
            for (name, scheme), argument in zip(used.items(), arguments, strict=True)
        )

    def problem(
        self,
        operation: OperationContract,
        message: str,
        *,
        conflict: bool = False,
        feature: ClientFeature | None = None,
    ) -> None:
        """Keep a deterministic diagnostic on the operation that requires invalid security, by the feature it uses."""
        self.problems.append(
            Diagnostic(
                code="E_CONFIG_CONFLICT" if conflict else "E_METADATA_REQUIRED",
                severity="error",
                stage="target",
                message=f"The security of {label(operation)} {message}",
                source_pointer=operation.id.use_site.pointer,
                operation=operation_ref(operation.id),
                feature=feature,
            )
        )

    def unavailable(
        self, operation: OperationContract, name: object, declared: Mapping[str, WireDeclaration]
    ) -> ClientFeature | None:
        """Return the feature an unavailable scheme uses that the client does not support, or refuse the operation."""
        if (feature := None if (found := declared.get(name)) is None else _feature(found)) is None:
            self.problem(operation, f"requires the unavailable scheme {stable_repr(name)}")
        return feature

    def binding(self, operation: OperationContract) -> SecurityBinding | None:
        """Preserve absent, empty, and ordered AND/OR requirements, naming schemes of the using document.

        An alternative that names a scheme using a feature the client does not support is left out while another
        alternative remains, as a call can authenticate with that one; with none left, each such scheme refuses the
        operation by its feature.
        """
        value = operation_literal(operation, "security")
        if value is None:
            return None
        schemes = operation_schemes(self.catalogues, operation)
        declared_schemes = operation_schemes(self.declared, operation)
        if (declared := requirement_alternatives(value)) is None:
            self.problem(operation, "must be an array of requirement objects")
            return None
        alternatives: list[tuple[SecurityRequirement, ...]] = []
        unsupported: dict[str, ClientFeature] = {}
        for alternative in declared:
            if alternative is None:
                self.problem(operation, "must contain only requirement objects")
                continue
            owners: dict[tuple[str, str], list[SecurityRequirement]] = {}
            left_out = False
            for name, scopes in alternative:
                if not isinstance(name, str) or not isinstance(scheme := schemes.get(name), SecurityScheme):
                    if (feature := self.unavailable(operation, name, declared_schemes)) is not None:
                        unsupported.setdefault(stable_repr(name), feature)
                        left_out = True
                    continue
                declaration = declared_schemes[name]
                version = self.versions.of(operation.id.use_site)
                if (invalid := scope_problem(name, declaration, scopes, version)) is not None:
                    self.problem(operation, invalid)
                    continue
                required = tuple(sorted(set(scopes or ())))
                owner = owners.setdefault(parameter_key(scheme.location, scheme.wire_name), [])
                if owner and owner[0].scheme.kind != scheme.kind:
                    self.problem(
                        operation, f"has overlapping {scheme.location} ownership in one AND alternative", conflict=True
                    )
                owner.append(SecurityRequirement(scheme=scheme, required_scopes=required))
            if not left_out:
                alternatives.extend(_shared(owners.values()))
        if not alternatives:
            for named, feature in unsupported.items():
                self.problem(operation, f"requires the unavailable scheme {named}", feature=feature)
        return SecurityBinding(schemes=tuple(schemes.values()), alternatives=tuple(alternatives))
