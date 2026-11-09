"""Builtin Ed25519 and RSA-PSS signature profiles, whose keys wrap cryptography's public keys.

Only the webhook keys and helpers of a package that selects one of these profiles import this module, so no other
package imports cryptography. A wrapped public key is used as given: it is never copied, serialized, closed, shown, or
derived from a private key.
"""

from __future__ import annotations

from operator import attrgetter
from typing import TYPE_CHECKING, Any, Final, final

from cryptography.exceptions import InvalidSignature, UnsupportedAlgorithm
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.asymmetric.padding import MGF1, PSS
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey
from cryptography.hazmat.primitives.hashes import SHA256

from .signatures import SignatureAlgorithm, UnsupportedKeyError
from .webhook_keys import SealedKey, checked_key_id

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = ("ED25519", "RSA_PSS_SHA256", "Ed25519Key", "RSAPSSKey")

_PSS: Final = PSS(mgf=MGF1(SHA256()), salt_length=32)
_SHA256: Final = SHA256()


def _checked(id: object, public_key: object, kind: type) -> str:  # noqa: A002
    """Return a valid id of a public key, refusing another class of key by its name only.

    The key's real class must derive from the cryptography class, so an object claiming that class is refused; a class
    registered with the cryptography class is accepted, since the application supplies its own keys.
    """
    checked = checked_key_id(id)
    if not issubclass(type(public_key), kind):
        msg = f"public_key must be an {kind.__name__}"
        raise TypeError(msg)
    return checked


@final
class Ed25519Key(SealedKey):
    """An Ed25519 public key for raw 64-byte signatures, and its id; the key never appears in a representation."""

    __slots__ = ("_public_key",)

    _public_key: Ed25519PublicKey

    def __init__(self, *, id: str, public_key: Ed25519PublicKey) -> None:  # noqa: A002
        """Keep the id and the public key itself, refusing a private key, bytes, text, or any other type."""
        object.__setattr__(self, "_id", _checked(id, public_key, Ed25519PublicKey))
        object.__setattr__(self, "_public_key", public_key)

    @property
    def public_key(self) -> Ed25519PublicKey:
        """Return the public key."""
        return self._public_key


@final
class RSAPSSKey(SealedKey):
    """An RSA public key and its id for RSA-PSS with SHA-256, MGF1-SHA-256, and a 32-byte salt.

    The key never appears in a representation.
    """

    __slots__ = ("_public_key",)

    _public_key: RSAPublicKey

    def __init__(self, *, id: str, public_key: RSAPublicKey) -> None:  # noqa: A002
        """Keep the id and the public key itself, refusing a private key, bytes, text, or any other type."""
        object.__setattr__(self, "_id", _checked(id, public_key, RSAPublicKey))
        object.__setattr__(self, "_public_key", public_key)

    @property
    def public_key(self) -> RSAPublicKey:
        """Return the public key."""
        return self._public_key


def _verified(verify: Callable[[bytes], None], signatures: tuple[bytes, ...]) -> bool:
    """Return whether a signature verifies, trying each in order; only InvalidSignature moves on to the next.

    UnsupportedAlgorithm raises UnsupportedKeyError after its handler ends, so neither the backend's error nor its
    frames are chained to it; any other error propagates.
    """
    for signature in signatures:
        try:
            verify(signature)
        except InvalidSignature:
            continue
        except UnsupportedAlgorithm:
            break
        return True
    else:
        return False
    raise UnsupportedKeyError


def _public_key(*arguments: Any) -> Callable[[Ed25519Key | RSAPSSKey, tuple[bytes, ...], tuple[bytes, ...]], bool]:
    def matches(key: Ed25519Key | RSAPSSKey, parts: tuple[bytes, ...], signatures: tuple[bytes, ...]) -> bool:
        """Verify the joined parts with the key's public key and the profile's arguments, as `_verified` tries them."""
        signed, public_key = b"".join(parts), key.public_key
        return _verified(lambda signature: public_key.verify(signature, signed, *arguments), signatures)

    return matches


ED25519: Final = SignatureAlgorithm(key_type=Ed25519Key, size=64, key_id=attrgetter("id"), matches=_public_key())
RSA_PSS_SHA256: Final = SignatureAlgorithm(
    key_type=RSAPSSKey, size=None, key_id=attrgetter("id"), matches=_public_key(_PSS, _SHA256)
)
