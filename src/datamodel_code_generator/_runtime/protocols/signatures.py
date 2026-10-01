"""Builtin signature profiles: their signed parts, signature encodings, and the HMAC algorithms.

Only generated webhook helpers import this module, so ordinary clients never load HMAC or base64. The public-key
algorithms live in their own module, which alone imports cryptography.
"""

from __future__ import annotations

import hmac
import re
from base64 import b64decode, urlsafe_b64decode
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Final, Generic, Literal, TypeAlias, final

from typing_extensions import TypeVar

from .webhook_keys import HmacKey

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

__all__ = (
    "DELIVERY_ID",
    "HMAC_SHA256",
    "HMAC_SHA512",
    "RAW_BODY",
    "TIMESTAMP",
    "FactField",
    "SignatureAlgorithm",
    "SignatureProfile",
    "SignedPart",
    "TimestampField",
    "UnsupportedKeyError",
    "satisfied",
    "signature_bytes",
)

K = TypeVar("K")
Encoding: TypeAlias = Literal["hex", "base64", "base64url"]


class SignedPart(Enum):
    """A dynamic signed part: the raw body, or the original bytes of the timestamp or delivery-id header."""

    RAW_BODY = "raw-body"
    TIMESTAMP = "timestamp"
    DELIVERY_ID = "delivery-id"


RAW_BODY: Final = SignedPart.RAW_BODY
TIMESTAMP: Final = SignedPart.TIMESTAMP
DELIVERY_ID: Final = SignedPart.DELIVERY_ID
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


@dataclass(frozen=True, slots=True, kw_only=True)
class FactField:
    """A signed header fact: its lowercase name and the length or byte set its original bytes must have."""

    header: str
    fixed_bytes: int | None = None
    ascii_bytes: bytes | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class TimestampField(FactField):
    """The signed timestamp header and the unit of its decimal value."""

    unit: Literal["seconds", "milliseconds"]


@dataclass(frozen=True, slots=True, kw_only=True)
class SignatureProfile(Generic[K]):
    """How a helper's signature header, facts, and signed bytes are read; generated, so never validated again.

    Header names are lowercase. A literal part is its ASCII bytes, and a None separator keeps each header value whole.
    """

    algorithm: SignatureAlgorithm[K]
    header: str
    encoding: Encoding
    prefix: str
    separator: str | None
    key_id: str | None
    timestamp: TimestampField | None
    delivery_id: FactField | None
    parts: tuple[SignedPart | bytes, ...]


def signature_bytes(profile: SignatureProfile[K], element: str) -> bytes | None:
    """Return the signature one header element carries, or None for an empty, unprefixed, or misencoded element.

    Spaces and tabs around the element are ignored; the prefix must match exactly, and the decoded signature must have
    the algorithm's size, or any nonzero size when each key decides its own.
    """
    text = element.strip(" \t")
    if not text or not text.startswith(profile.prefix):
        return None
    encoded = text[len(profile.prefix) :]
    if not _ENCODED[profile.encoding].fullmatch(encoded):
        return None
    value = _DECODERS[profile.encoding](encoded)
    if (size := profile.algorithm.size) is None:
        return value or None
    return value if len(value) == size else None


def satisfied(field: FactField, value: bytes) -> bool:
    """Return whether a fact's original bytes have its declared length and belong to its declared byte set."""
    if field.fixed_bytes is not None and len(value) != field.fixed_bytes:
        return False
    return field.ascii_bytes is None or not value.translate(None, field.ascii_bytes)
