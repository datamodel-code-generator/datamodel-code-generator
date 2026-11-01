"""The request positions that carry credentials: the credential headers and the positions of declared schemes.

The client core reads the optional security modules, copied only with schemes, through the contracts declared here
for type checkers alone, so this module never imports HTTPX2 at run time and the generator may import it.
"""

from __future__ import annotations

from functools import lru_cache
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Hashable, Mapping
    from typing import Any, Protocol, TypeAlias

    import httpx2

    from .urls import Origin

    class CredentialPosition(Hashable, Protocol):
        """Where a declared security scheme places its credential, hashable as the caches key on it.

        A scheme that cannot place one has no location.
        """

        @property
        def location(self) -> str | None:
            """Return `header`, `query`, or `cookie`, or None."""
            ...

        @property
        def wire_name(self) -> str:
            """Return the header, query, or cookie name."""
            ...

    Placement: TypeAlias = tuple[CredentialPosition, object]

    Send: TypeAlias = Callable[[httpx2.Request], httpx2.Response]
    AsyncSend: TypeAlias = Callable[[httpx2.Request], Awaitable[httpx2.Response]]

    class Credentials(Protocol):
        """The credentials a generated client was given, by scheme name, which the package's auth module places."""

        @property
        def values(self) -> Mapping[str, object]:
            """Return the credentials given, by scheme name."""
            ...

        def selected(self, binding: Any) -> tuple[Placement, ...] | None:
            """Return the placements of an operation's first alternative the credentials satisfy, or None."""
            ...

        def auth(  # noqa: PLR0913
            self,
            placements: Any,
            *,
            origin: Origin | None,
            replayable: Callable[[httpx2.Request], bool],
            challenge_less: bool,
            send: Send | None = None,
            async_send: AsyncSend | None = None,
        ) -> httpx2.Auth:
            """Return the HTTPX2 Auth that places a call's credentials on its requests."""
            ...

    class SecurityAlternatives(Protocol):
        """An operation's declared security: its OR alternatives of AND requirements."""

        @property
        def alternatives(self) -> tuple[tuple[object, ...], ...]:
            """Return the alternatives in their order; an empty one is anonymous."""
            ...


CREDENTIAL_HEADERS: Final = frozenset({"authorization", "proxy-authorization", "cookie", "cookie2"})


def cookie_name(pair: str) -> str:
    """Return the name of a `name=value` pair of a Cookie header, without the whitespace around it."""
    return pair.partition("=")[0].strip()


def positional(placements: tuple[Placement, ...]) -> bool:
    """Return whether a placement sends a credential elsewhere than in Authorization, which HTTPX2 keeps in origin."""
    return any(scheme.location != "header" or scheme.wire_name.lower() != "authorization" for scheme, _ in placements)


@lru_cache(maxsize=32)
def secret_names(schemes: tuple[CredentialPosition, ...]) -> tuple[frozenset[str], frozenset[str]]:
    """Return the lowercase header names and the query names that carry credentials in a package's requests.

    They are the credential and cookie headers and the positions of the package's declared security schemes, however
    a request came to fill them.
    """
    headers, query, _ = protected_positions(schemes)
    return CREDENTIAL_HEADERS | headers, query


@lru_cache(maxsize=32)
def protected_positions(
    schemes: tuple[CredentialPosition, ...],
) -> tuple[frozenset[str], frozenset[str], frozenset[str]]:
    """Return the lowercase header names, query names, and cookie names of a package's declared security schemes.

    The Authorization header is left out, since HTTPX2 drops it from a redirect to another origin itself.
    """
    return (
        frozenset(
            name
            for scheme in schemes
            if scheme.location == "header" and (name := scheme.wire_name.lower()) != "authorization"
        ),
        frozenset(scheme.wire_name for scheme in schemes if scheme.location == "query"),
        frozenset(scheme.wire_name for scheme in schemes if scheme.location == "cookie"),
    )


def credentialed(request: httpx2.Request, schemes: tuple[CredentialPosition, ...]) -> bool:
    """Return whether a request carries a value at a declared scheme's header, query, or cookie, Authorization aside."""
    headers, query, cookies = protected_positions(schemes)
    sent = request.headers
    if any(name in sent for name in headers) or (query and any(name in request.url.params for name in query)):
        return True
    return bool(cookies) and any(
        cookie_name(pair) in cookies for value in sent.get_list("cookie") for pair in value.split(";")
    )
