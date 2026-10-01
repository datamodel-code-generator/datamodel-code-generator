"""Concrete keys of builtin webhook signature profiles, whose material never appears in a representation."""

from __future__ import annotations

import re
from typing import Final, final

from ..client.errors import ProtocolConfigurationError
from .records import Sealed

__all__ = ("HmacKey", "checked_key_id")

_FORBIDDEN: Final = re.compile(r"[\x00\r\n]")


def checked_key_id(value: object) -> str:
    """Return a key id: a nonempty string without NUL, CR, or LF; another type raises TypeError."""
    if not isinstance(value, str):
        msg = "id must be a string"
        raise TypeError(msg)
    if not value or _FORBIDDEN.search(value):
        raise ProtocolConfigurationError(field_path=("id",), condition="invalid_value")
    return value


@final
class HmacKey(Sealed):
    """An HMAC secret and its id; the secret goes to HMAC unchanged and never appears in a representation."""

    __slots__ = ("_id", "_secret")

    _id: str
    _secret: bytes

    def __init__(self, *, id: str, secret: bytes) -> None:  # noqa: A002
        """Keep the id and the exact secret bytes, refusing a bytearray, a string, or any other type."""
        checked = checked_key_id(id)
        if type(secret) is not bytes:
            msg = "secret must be bytes"
            raise TypeError(msg)
        for name, item in (("_id", checked), ("_secret", secret)):
            object.__setattr__(self, name, item)

    @property
    def id(self) -> str:
        """Return the id that key-id headers and matched_key_id name."""
        return self._id

    @property
    def secret(self) -> bytes:
        """Return the secret bytes."""
        return self._secret

    def __repr__(self) -> str:
        """Name the id only, never the secret."""
        return f"HmacKey(id={self._id!r})"
