"""Immutable security declarations compiled for a generated client."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import TYPE_CHECKING, Final, Literal, TypeAlias

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping

    import httpx2

    from .urls import Origin

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


Placement: TypeAlias = tuple[SecurityScheme, object]
Send: TypeAlias = "Callable[[httpx2.Request], httpx2.Response]"
AsyncSend: TypeAlias = "Callable[[httpx2.Request], Awaitable[httpx2.Response]]"


class Credentials:
    """The credentials a generated client was given, by scheme name; the package's auth module places them."""

    def __init__(self, values: Mapping[str, object]) -> None:
        """Keep the credentials given, leaving out None."""
        self.values = {name: value for name, value in values.items() if value is not None}

    def selected(self, binding: SecurityBinding) -> tuple[Placement, ...] | None:
        """Return the placements of an operation's first alternative the credentials satisfy, or None for none.

        An anonymous alternative needs no credentials, and an operation declaring empty security takes none.
        """
        values = self.values
        for alternative in binding.alternatives:
            if all(item.scheme.name in values for item in alternative):
                return tuple((item.scheme, values[item.scheme.name]) for item in alternative)
        return () if not binding.alternatives else None

    def auth(  # noqa: PLR0913
        self,
        placements: tuple[Placement, ...],
        *,
        origin: Origin | None,
        replayable: Callable[[httpx2.Request], bool],
        challenge_less: bool,
        send: Send | None = None,
        async_send: AsyncSend | None = None,
    ) -> httpx2.Auth:
        """Return the HTTPX2 Auth that places a call's credentials on its requests."""
        raise NotImplementedError


def positional(placements: tuple[Placement, ...]) -> bool:
    """Return whether a placement sends a credential elsewhere than in Authorization, which HTTPX2 keeps in origin."""
    return any(scheme.location != "header" or scheme.wire_name.lower() != "authorization" for scheme, _ in placements)


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


@lru_cache(maxsize=32)
def protected_positions(
    schemes: tuple[SecuritySchemeEntry, ...],
) -> tuple[frozenset[str], frozenset[str], frozenset[str]]:
    """Return the lowercase header names, query names, and cookie names of a package's declared security schemes.

    The Authorization header is left out, since HTTPX2 drops it from a redirect to another origin itself.
    """
    declared = [scheme for scheme in schemes if isinstance(scheme, SecurityScheme)]
    return (
        frozenset(
            name
            for scheme in declared
            if scheme.location == "header" and (name := scheme.wire_name.lower()) != "authorization"
        ),
        frozenset(scheme.wire_name for scheme in declared if scheme.location == "query"),
        frozenset(scheme.wire_name for scheme in declared if scheme.location == "cookie"),
    )
