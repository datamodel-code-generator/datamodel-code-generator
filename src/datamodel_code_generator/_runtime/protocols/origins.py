"""Effective HTTP origins exactly as the client's URL rules produce them."""

from __future__ import annotations

from dataclasses import dataclass

from .records import record_string

__all__ = ("Origin",)


def _origin(scheme: object, host: object, port: object) -> None:
    from ..client.urls import URLValidationError, canonical_origin, origin_text  # noqa: PLC0415 - Parse URLs only here.

    names = (record_string(scheme, "scheme"), record_string(host, "host"))
    if type(port) is not int:
        msg = "port must be an integer"
        raise TypeError(msg)
    origin = (*names, port)
    try:
        valid = canonical_origin.__wrapped__(origin_text.__wrapped__(origin)) == origin
    except URLValidationError:
        valid = False
    if not valid:
        msg = "origin must be the scheme, host, and port that the client's URL rules produce"
        raise ValueError(msg)


@dataclass(frozen=True, slots=True, kw_only=True)
class Origin:
    """An effective HTTP origin exactly as the client's URL rules produce it; other spellings are rejected.

    The host is lowercase ASCII, with IDNA labels already encoded, and an IPv6 address has no brackets.
    """

    scheme: str
    host: str
    port: int

    def __post_init__(self) -> None:
        """Require the scheme, host, and effective port that interpreting this origin's URL gives back unchanged."""
        _origin(self.scheme, self.host, self.port)
