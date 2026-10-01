"""Immutable security declarations compiled for a generated client."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Final, Literal, TypeAlias

CREDENTIAL_HEADERS: Final = frozenset({"authorization", "proxy-authorization", "cookie", "cookie2"})


@dataclass(frozen=True, slots=True, kw_only=True)
class SecurityScheme:
    """A declared credential's material kind and exact outgoing wire position."""

    name: str
    kind: Literal["api_key", "basic", "bearer"]
    location: Literal["header", "query", "cookie"]
    wire_name: str


@dataclass(frozen=True, slots=True, kw_only=True)
class UnavailableSecurityScheme:
    """A declared name whose unsupported or unresolved scheme cannot supply credentials."""

    name: str


SecuritySchemeEntry: TypeAlias = SecurityScheme | UnavailableSecurityScheme


@dataclass(frozen=True, slots=True, kw_only=True)
class SecurityRequirement:
    """One usable scheme and its canonical required scopes in an AND alternative."""

    scheme: SecurityScheme
    required_scopes: tuple[str, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class SecurityBinding:
    """An operation's source-context catalogue and ordered OR alternatives."""

    schemes: tuple[SecuritySchemeEntry, ...]
    alternatives: tuple[tuple[SecurityRequirement, ...], ...]


@lru_cache(maxsize=32)
def secret_names(schemes: tuple[SecuritySchemeEntry, ...]) -> tuple[frozenset[str], frozenset[str]]:
    """Return the lowercase header names and the query names that carry credentials in a package's requests.

    They are the credential and cookie headers and the positions of the package's declared security schemes, however
    a request came to fill them.
    """
    declared = [scheme for scheme in schemes if isinstance(scheme, SecurityScheme)]
    return (
        CREDENTIAL_HEADERS.union(scheme.wire_name.lower() for scheme in declared if scheme.location == "header"),
        frozenset(scheme.wire_name for scheme in declared if scheme.location == "query"),
    )
