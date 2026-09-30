"""Immutable security declarations compiled for a generated client."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypeAlias


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
