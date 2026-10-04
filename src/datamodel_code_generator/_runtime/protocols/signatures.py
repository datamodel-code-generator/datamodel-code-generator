"""Signature encodings and builtin HMAC algorithms.

Only generated webhook helpers import this module, so ordinary clients never load HMAC or base64. The public-key
algorithms live in their own module, which alone imports cryptography.
"""

from __future__ import annotations

import hmac
import re
from base64 import b64decode, urlsafe_b64decode
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Generic, Literal, TypeAlias, final

from typing_extensions import TypeVar

from .webhook_keys import HmacKey

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

__all__ = (
    "HMAC_SHA256",
    "HMAC_SHA512",
    "SignatureAlgorithm",
    "UnsupportedKeyError",
    "signature_bytes",
)

K = TypeVar("K")
Encoding: TypeAlias = Literal["hex", "base64", "base64url"]


_ENCODED: Final[Mapping[Encoding, re.Pattern[str]]] = {
    "hex": re.compile(r"(?:[0-9A-Fa-f]{2})+"),
    "base64": re.compile(r"(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?"),
    "base64url": re.compile(r"(?:[A-Za-z0-9_-]{4})*(?:[A-Za-z0-9_-]{2}(?:==)?|[A-Za-z0-9_-]{3}=?)?"),
}
_DECODERS: Final[Mapping[Encoding, Callable[[str], bytes]]] = {
    "hex": bytes.fromhex,
    "base64": b64decode,
    "base64url": lambda text: urlsafe_b64decode(text + "=" * (-len(text) % 4)),
}


class UnsupportedKeyError(Exception):
    """Raised by an algorithm whose backend cannot verify with a key; the call reports the key's wrong capability."""


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class SignatureAlgorithm(Generic[K]):
    """A builtin signature algorithm: its key class, signature size, the id of a key, and verification with one key.

    A None size leaves the size to each key. `matches` returns whether a key signed the parts, in order, as one of the
    signatures, trying them in order, and raises UnsupportedKeyError for a key its backend cannot use.
    """

    key_type: type[K]
    size: int | None
    key_id: Callable[[K], str]
    matches: Callable[[K, tuple[bytes, ...], tuple[bytes, ...]], bool]


def _hmac_id(key: HmacKey) -> str:
    return key.id


def _hmac(digest: Literal["sha256", "sha512"]) -> Callable[[HmacKey, tuple[bytes, ...], tuple[bytes, ...]], bool]:
    def matches(key: HmacKey, parts: tuple[bytes, ...], signatures: tuple[bytes, ...]) -> bool:
        """Stream the parts through one HMAC of the key and compare its digest with each signature in constant time."""
        mac = hmac.new(key.secret, digestmod=digest)
        for part in parts:
            mac.update(part)
        expected = mac.digest()
        return any(hmac.compare_digest(expected, signature) for signature in signatures)

    return matches


HMAC_SHA256: Final = SignatureAlgorithm(key_type=HmacKey, size=32, key_id=_hmac_id, matches=_hmac("sha256"))
HMAC_SHA512: Final = SignatureAlgorithm(key_type=HmacKey, size=64, key_id=_hmac_id, matches=_hmac("sha512"))


def signature_bytes(encoding: Encoding, prefix: str, size: int | None, element: str) -> bytes | None:
    """Decode a prefixed signature, requiring the algorithm's size or a nonempty public-key signature."""
    text = element.strip(" \t")
    if not text or not text.startswith(prefix):
        return None
    encoded = text[len(prefix) :]
    if not _ENCODED[encoding].fullmatch(encoded):
        return None
    value = _DECODERS[encoding](encoded)
    if size is None:
        return value or None
    return value if len(value) == size else None
