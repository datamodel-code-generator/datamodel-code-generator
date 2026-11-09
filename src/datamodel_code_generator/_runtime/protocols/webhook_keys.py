"""Concrete keys of builtin webhook signature profiles, whose material never appears in a representation."""

from __future__ import annotations

import re
from typing import Final, final

from ..client.errors import ConfigurationError
from .records import Sealed

__all__ = ("HmacKey", "SealedKey", "checked_key_id")

_FORBIDDEN: Final = re.compile(r"[\x00\r\n]")


def checked_key_id(value: object) -> str:
    """Return a key id: a nonempty string without NUL, CR, or LF; another type raises TypeError."""
    if not isinstance(value, str):
        msg = "id must be a string"
        raise TypeError(msg)
    if not value or _FORBIDDEN.search(value):
        raise ConfigurationError(field_path=("id",), reason="invalid_value")
    return value


class SealedKey(Sealed):
    """A webhook key and its id, whose representation names the id only, never the key material."""

    __slots__ = ("_id",)

    _id: str

    @property
    def id(self) -> str:
        """Return the id that key-id headers and matched_key_id name."""
        return self._id

    def __repr__(self) -> str:
        """Name the id only, never the key."""
        return f"{type(self).__name__}(id={self._id!r})"


@final
class HmacKey(SealedKey):
    """An HMAC secret and its id; the secret goes to HMAC unchanged and never appears in a representation."""

    __slots__ = ("_secret",)

    _secret: bytes

    def __init__(self, *, id: str, secret: bytes) -> None:  # noqa: A002
        """Keep the id and the exact secret bytes, refusing a bytearray, a string, or any other type."""
        checked = checked_key_id(id)
        if type(secret) is not bytes:
            msg = "secret must be bytes"
            raise TypeError(msg)
        object.__setattr__(self, "_id", checked)
        object.__setattr__(self, "_secret", secret)

    @property
    def secret(self) -> bytes:
        """Return the secret bytes."""
        return self._secret
