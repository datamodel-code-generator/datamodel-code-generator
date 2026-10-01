"""Verify generated Ed25519 and RSA-PSS webhook helpers against published vectors and vectors authored elsewhere.

Published vectors are RFC 8032 section 7.1 Ed25519 TEST 1 and TEST 2, and the RSA-PSS vectors of Wycheproof by tcId,
with their test group's publicKeyPem: C2SP/wycheproof commit 3fa63dd0344abb611f1fb1d77e119938603ea230, file
testvectors_v1/rsa_pss_2048_sha256_mgf1_32_test.json, whose SHA-256 is
7f6efafc160f4816b96cbf1c12188a31051d7e3f001e27505d9edb5f2a0e325c. Authored vectors were signed with OpenSSL 3.0.2,
outside the runtime, by throwaway keys from `openssl genpkey -algorithm ed25519` and `openssl genpkey -algorithm RSA
-pkeyopt rsa_keygen_bits:<bits>`, of which only the public keys from `openssl pkey -pubout` (the raw 32 bytes for
Ed25519) are kept; each vector's `source` names the signing command. Expected signatures are literals, so no expected
value comes from the code under test.
"""

from __future__ import annotations

import asyncio
import copy
import pickle
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, Final, get_type_hints

from cryptography.exceptions import InvalidSignature, UnsupportedAlgorithm
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey, RSAPublicKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat, load_pem_public_key

from tests.data.python.client_runtime import describe, record
from tests.data.python.client_webhooks import (
    GITHUB,
    KEYED,
    PUBLIC_MODULES,
    STANDARD,
    Vector,
    Webhooks,
    failure,
    imports,
    outcome,
    reachable,
)

if TYPE_CHECKING:
    from types import ModuleType

_WYCHEPROOF_KEY: Final = load_pem_public_key(
    b"-----BEGIN PUBLIC KEY-----\n"
    b"MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAorRRoH0KpfluRVZxUTVQ\n"
    b"UUqKW0YuvvcXCU+h/ugiJOY3+XRtP3yv0xh42AMltu9aFwD2WQO0aUKeidbqyIRQ\n"
    b"l7WrOTGJ25JRLtincRoSU/rNIPecFegkfz0+QuRuSMmOJUov6XZTE6A+/48X4aAp\n"
    b"OXofomqNzib0kO2BKZYV2YFMItphBCjgnH2WWFlCZvXAIdD87KCNlFoSvoLeTR7O\n"
    b"a0wDFFtdNJXU7VQR64eNrwX9evw+Ca2g8RJkIvWQl1oZaYFvSGmLy7obTZyuedRg\n"
    b"2Pn4Xnl1AF2bwixOWsD3waRdElaaYoB9O5oC5aUw53MGb0U9H1tMLpz3ggKD90K5\n"
    b"1QIDAQAB\n"
    b"-----END PUBLIC KEY-----\n"
)
_RSA_2048_KEY: Final = load_pem_public_key(
    b"-----BEGIN PUBLIC KEY-----\n"
    b"MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAydNh2tgcIk+tfGWeh4x+\n"
    b"Xi59vZOwzSShRmz22jZONmzG5pnWjyuTgHkv3SftqUrr5O7lL1n0Z3uARjrBTbKB\n"
    b"YpUtF0ZoupmxGcxLop6aA8uaT78xDl6h/dmmKgqPEq0xjVdGIhr6SxFx2U2zj/zo\n"
    b"KxJdDqGZh6wLZ3SBAsi3gOsRtYLJ1pz5vt8OpJI/ZRmZXXbCi9qBvflTb9y7q6ZN\n"
    b"bRExO1XmpqVeA0xSRtf3YqY/622O9T5iYKNuJexyK9JFAuYETJqhZdw2DhsA3jvg\n"
    b"aPFOR+Fg8B1g9BMj8fTnp/XloT4GJwQrqdCivsuPULyOyQwyyviFpr45a9nGsgwm\n"
    b"QQIDAQAB\n"
    b"-----END PUBLIC KEY-----\n"
)
_RSA_3072_KEY: Final = load_pem_public_key(
    b"-----BEGIN PUBLIC KEY-----\n"
    b"MIIBojANBgkqhkiG9w0BAQEFAAOCAY8AMIIBigKCAYEAocIAx2TjRBSofQXrogvT\n"
    b"4+mBpXVvoQBTOeovRSB7mkVGKT3FXrdDKswVkpTuMThXiWFi85IJRVE+qfPaW5j6\n"
    b"d/CrB45veFUCaDpAAs2WPmP55TzgCmrGK7De8IYnmrYBLBtVYBDvVNOkBc/sYIYs\n"
    b"cVGlCTFGsLdlYXvJr6CDxItKkbssIK41wLrYEzZiJdH/aA2XfckfSxJsIE7NXv5h\n"
    b"IggCGkCRFWV4Zwoue0UIQeaJpUdp24KhGgxhmnwzQ6W42QyRXPoUQ2iafchBjH94\n"
    b"lneh6SP2VOcvXnRaW8xR/cr/is2OLDT3UXvr8MbkWB1MClQXZc1/tXnug3nVrkf4\n"
    b"/tx46CAxs2LJT/DdjqNFxgvdbIaJUF2UuaovCWoMm5Mg/AXUNhi+igGcNR8XqCsJ\n"
    b"C5zZt5Ig81F+1QBaQnZ0g/nXfxAcISupkgI4+p4F3KaSJf5JrYDzmlgjm19B+LBJ\n"
    b"BSVEBqWn8BeUZLX/XcdU1wFNL/ugd6FcVs1fnX8Sote7AgMBAAE=\n"
    b"-----END PUBLIC KEY-----\n"
)
_ED25519_KEY: Final = Ed25519PublicKey.from_public_bytes(
    bytes.fromhex("f02bb4498ff4373d9f2f746ab308609c521aa1dc5d9527a2ed36be49582d3a1f")
)
_RFC_8032_TEST_1_KEY: Final = Ed25519PublicKey.from_public_bytes(
    bytes.fromhex("d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a")
)
_RFC_8032_TEST_2_KEY: Final = Ed25519PublicKey.from_public_bytes(
    bytes.fromhex("3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c")
)
_ZERO_ED25519: Final = "v1a," + "A" * 86 + "=="
_MARKER: Final = "MARKER-PUBLIC-KEY"


@dataclass(frozen=True, slots=True)
class Signed:
    """One delivery signed by a private key: where its signature comes from, the helper, the public key, and inputs."""

    source: str
    helper: str
    public_key: object
    body: bytes
    headers: tuple[tuple[str, str], ...]
    now: datetime
    key_id: str = "active"

    changed = Vector.changed


RFC_8032: Final = (
    Signed(
        source="RFC 8032 section 7.1 Ed25519 TEST 1, the empty message",
        helper="rfc8032.ed25519",
        public_key=_RFC_8032_TEST_1_KEY,
        body=b"",
        headers=(
            (
                "X-Signature",
                "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33bacc61e39701cf9b46b"
                "d25bf5f0595bbe24655141438e7a100b",
            ),
        ),
        now=STANDARD.now,
    ),
    Signed(
        source="RFC 8032 section 7.1 Ed25519 TEST 2, the message 0x72",
        helper="rfc8032.ed25519",
        public_key=_RFC_8032_TEST_2_KEY,
        body=b"\x72",
        headers=(
            (
                "X-Signature",
                "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da085ac1e43e15996e458f3613d0f11d8c"
                "387b2eaeb4302aeeb00d291612bb0c00",
            ),
        ),
        now=STANDARD.now,
    ),
)


def _wycheproof(case: int, comment: str, body: bytes, signature: str) -> Signed:
    return Signed(
        source=f"Wycheproof RSA-PSS 2048 SHA-256 MGF1 32 tcId {case}, {comment}",
        helper="wycheproof.rsa",
        public_key=_WYCHEPROOF_KEY,
        body=body,
        headers=(("X-Signature", signature),),
        now=STANDARD.now,
    )


WYCHEPROOF_VALID: Final = (
    _wycheproof(
        1,
        "valid signature of the empty message",
        b"",
        "4f01e0c12b08625ecac89a69231906edf826380f37c959a96690d046316d68ffce9d5c471694fcebfc6b45534864689256e4fc81c78e"
        "583f675d0c94b449647451e81beff01a11a516d5e5ce3f1a910437cb8a3a5096b19fb15f4524a35b23d89cdba12cf5b71aac1047b28c"
        "562df7c5542c34ce23a182cf7e0e231934b17294799d44877a1d68ef1b8f073619b7618e6b7c22db20030d98cf591ffc3d4da5f58613"
        "ecd5ecfc3b40a1d02f40891ca43695cd4c088b05a8054c89c595a47e274816f35384226f74459ee63e25a1bfc03c360490552ec38343"
        "f8ace502f065303b00bc0ec320711b211fde92e57feb9013c3609342495ec0d7cabdec21e54acc38",
    ),
    _wycheproof(
        4,
        "valid signature of 313233343030",
        b"123400",
        "68caf07e71ee654ffabf07d342fc4059deb4f7e5970746c423b1e8f668d5332275cc35eb61270aebd27855b1e80d59def47fe8882867"
        "fd33c2308c91976baa0b1df952caa78db4828ab81e79949bf145cbdfd1c4987ed036f81e8442081016f20fa4b587574884ca6f604595"
        "9ce3501ae7c02b1902ec1d241ef28dee356c0d30d28a950f1fbc683ee7d9aad26b048c13426fe3975d5638afeb5b9c1a99d162d3a581"
        "0e8b074d7a2eae2be52b577151f76e1f734b0a956ef4f22be64dc20a81ad1316e4f79dff5fc41fc08a20bc612283a88415d41595bfea"
        "66d59de7ac12e230f72244ad9905aef0ead3fa41ed70bf4218863d5f041292f2d14ce0a7271c6d36",
    ),
    _wycheproof(
        73,
        "valid signature of 313233343030 whose salt is all 0",
        b"123400",
        "1591ae743c58ceb723a76f502e21ff6a65c24cabf5f527bab5a6f2a69f20c776fd2251e43ad22e09b1486ceb1935b2dc2ade95e233f2"
        "96cc0e5a8af8109659be76b6bfdf37e14837fd6c34bfed1f19ec9d21f974b984fe4d4773896ebcc7fb862fd641cd0d77178485c70c2d"
        "68b4d9be1d863f6f254b77991fc9053f5d5415d1aa74ba9067e2e6607fb651638c9cc0430a40c9b691977b557a31d95a290a95b56ef2"
        "ec8e4313686a9c5ef48235912b210fdd2c50aafac28131104c795c42ae75810b0284b2d257e81ecac4240622ebc261ab8bceeeebe80f"
        "1cfa70f18d782aebb97d803ea3a895be541be6941df103eaaabd870848bfaf58cdaf6cecdd5a10bf",
    ),
)
WYCHEPROOF_INVALID: Final = (
    _wycheproof(
        62,
        "invalid: first byte of m_hash modified",
        b"123400",
        "67d1d1c0a398148625317c3f5e44b738bdf461c27a59594b39ebb2aebef233c7809379e54411411b82d2e7ac88f989b58373d532c758"
        "baea121878ce9759441738d121881c1fa2d04421f02dd565b12770d844611ed1873a0b64d822709a6b78d6d3892b294404bce6711001"
        "d6c3a54546c76a1d17819674b0be904497a233b466fe4becc832dee740f9ab79e5b9f5db0b0f9aac0084ba05cebf42303b5ca2ad95e3"
        "d61b29ed6475545c02e93e7b0e118af92f5cddb1faeb2cbc23c9e69c120e29df7fe31991e887b3b29e77688c60e80be65cccf3d7861a"
        "7a14c39e6a6e5645568e2cc5e4a17b75db1dd415aadb45e112a9b582b2ff6e82a43d7a7347b7b56d",
    ),
    _wycheproof(
        67,
        "invalid: s_len changed to 0",
        b"123400",
        "5e91b5dcbf02d6f19621d41a83dc8f15ea83c0edb83765ef029b0acac2e1ec8918b1d2afe1fadf11c48d27594cb9c01fed79d90e5d5a"
        "8085c438450111aa7d9fa39c2345b14fc3c2cb34128f86db5eb00bdf8dfe38d61f29a41fe31342e7aaefcb4b122eb5d63c2f5c263c8d"
        "f8450e9428ffef974d535818d51dc03a7d60c8b2d16c999ae46d73ab40515fe601d9b89b1d09c6d60cd51639a97c1d211e097609ba5e"
        "8c319c6fbd21b34a634ec8fb8971c5aae21c70b847a4539cc10dc314ddd8a9629e8a0e51c66c0cb61fd1f7228c01c6769190abe9bac9"
        "a3897800050014358594e0fb20dbb458b12aa1346826cc9f7e9c5352b073d62853dafe77c848cb1f",
    ),
    _wycheproof(
        71,
        "invalid: s_len changed to 33",
        b"123400",
        "563e94111eade9526d1f2e93da16ee171273291abfb90aeb94ae7b95f16395949ef3d3f0994852de035cebf8cd002b76579d0758325c"
        "6750ffc917be419174d255a2b798ce287f6240a97d4fcae47e88308658898ce37407e9684caca197c46ec9f66a0ba4e8aecf6a7ae749"
        "304fddf1ec4155a17de5d01117a3cbf2a34fa77d0556a39451b697c869e6fab3283541816bd6c7520b5eb0ee6b592a19331ffcccdcaa"
        "403f4f25e732a847ff260ec40ecfb52abc6f65f95d21715acd2c0dadb23d7e1c0fa8cac3e60c6f19b430f9c252ce1f392cd2f7bc87a4"
        "be0a4dc0b7f909afa7c25ed1b5be611bf86a648592786385f02c345eaedd03c4b0bb5bb758254d9d",
    ),
    _wycheproof(
        104,
        "invalid: prepending 0's to signature, 258 bytes",
        b"123400",
        "000068caf07e71ee654ffabf07d342fc4059deb4f7e5970746c423b1e8f668d5332275cc35eb61270aebd27855b1e80d59def47fe888"
        "2867fd33c2308c91976baa0b1df952caa78db4828ab81e79949bf145cbdfd1c4987ed036f81e8442081016f20fa4b587574884ca6f60"
        "45959ce3501ae7c02b1902ec1d241ef28dee356c0d30d28a950f1fbc683ee7d9aad26b048c13426fe3975d5638afeb5b9c1a99d162d3"
        "a5810e8b074d7a2eae2be52b577151f76e1f734b0a956ef4f22be64dc20a81ad1316e4f79dff5fc41fc08a20bc612283a88415d41595"
        "bfea66d59de7ac12e230f72244ad9905aef0ead3fa41ed70bf4218863d5f041292f2d14ce0a7271c6d36",
    ),
    _wycheproof(
        106,
        "invalid: truncated signature, 254 bytes",
        b"123400",
        "68caf07e71ee654ffabf07d342fc4059deb4f7e5970746c423b1e8f668d5332275cc35eb61270aebd27855b1e80d59def47fe8882867"
        "fd33c2308c91976baa0b1df952caa78db4828ab81e79949bf145cbdfd1c4987ed036f81e8442081016f20fa4b587574884ca6f604595"
        "9ce3501ae7c02b1902ec1d241ef28dee356c0d30d28a950f1fbc683ee7d9aad26b048c13426fe3975d5638afeb5b9c1a99d162d3a581"
        "0e8b074d7a2eae2be52b577151f76e1f734b0a956ef4f22be64dc20a81ad1316e4f79dff5fc41fc08a20bc612283a88415d41595bfea"
        "66d59de7ac12e230f72244ad9905aef0ead3fa41ed70bf4218863d5f041292f2d14ce0a7271c",
    ),
    _wycheproof(
        108,
        "invalid: PKCS #1 v1.5 signature with SHA-256",
        b"123400",
        "1758eb94588e6fc4f50c1be1afcaa41027869f304cad513b1fb12c2f446d63cdc05c4830a7e3e630da7b2da4f7867cc173bf6420f973"
        "2277282596de41ded32e21d0cc31441174da8765f57419c7764ea758f55bc17646eb100c435d1ac0eed6fc7ba6de5f832094ee2f4799"
        "79765e05ac9976788db3c241a9e32a0da864f0019a87646ba623d63f4411af5dee1be9ec488c7e3e1b231479de70b9ac5f78a17b1f41"
        "20aece45f26c07e7bb345fdfeb05e14bcaacc614672a465fc523624cb19f66f9c6c3f642b832ca44cb25176d679f0e05606c3fed022c"
        "ac24c2bf960a406d48818e3eb7ed53b0446032469047dfed95fc18088c92d91d93722c47f88163a8",
    ),
)
STANDARD_ED25519: Final = Signed(
    source=(
        "printf 'msg_ed25519.1614265330.{\"test\": 25519}' > msg; openssl pkeyutl -sign -rawin -inkey ed25519.pem "
        "-in msg | openssl base64 -A"
    ),
    helper="standard.ed25519",
    public_key=_ED25519_KEY,
    body=b'{"test": 25519}',
    headers=(
        ("webhook-id", "msg_ed25519"),
        ("webhook-timestamp", "1614265330"),
        (
            "webhook-signature",
            "v1a,jjpP+tiYWxKBWtQC+JFjRENufsh8A70KguvJzwWrv5wgB9gyE8d1VF1IdWuNU7fDXcs+vNHwb1WcHdrxaqIBCA==",
        ),
    ),
    now=STANDARD.now,
)
_PSS: Final = (
    "openssl dgst -sha256 -sigopt rsa_padding_mode:pss -sigopt rsa_pss_saltlen:32 -sigopt rsa_mgf1_md:sha256 -sign "
)
RSA_2048: Final = Signed(
    source=(
        f"printf '32503680000123.evt-9.{{\"test\": 2048}}' | {_PSS}rsa2048.pem | openssl base64 -A | tr '+/' '-_' | "
        "tr -d '='"
    ),
    helper="keyed.rsa",
    public_key=_RSA_2048_KEY,
    body=b'{"test": 2048}',
    headers=(
        ("X-Key-Id", "rsa-2048"),
        ("X-Timestamp", "32503680000123"),
        ("X-Delivery", "evt-9"),
        (
            "X-Signature",
            "v1="
            "XCyU-C0QfcyfhPZM-xJIsgDalHuC0iBa6nPA9Vz-duUg9HE5r9KEwkvrVwp06dB_Y6gGFycFjFHTUB79T4F-AnTe2LTMrU1a4OvK"
            "gqwFmd9qdZjaCUZLN3EElNI3KgXG3NsBXpDLCrXhjhnn26pdE_JJ-YeBlkgQQZ6ahrosOs-Y9gyU8_eDs5leBEk3tVs8aGlwLsLG"
            "hmnZZ7Mc0wh2d_LTkd_GdW-pPl8rWND4IasJdCxVnOmj7p7h0VmehmSpzKUxw0RfbF8U3Qk2a3m_huZ1SOpH6mkKdXoFWo4xNzC_"
            "NVbcBeZhSBtHFoovZzU6ZkILoyG8Ks9B6xFHhjrQxQ",
        ),
    ),
    now=KEYED.now,
    key_id="rsa-2048",
)
RSA_3072: Final = Signed(
    source=(
        f"printf '32503680000456.evt-10.{{\"test\": 3072}}' | {_PSS}rsa3072.pem | openssl base64 -A | tr '+/' '-_' | "
        "tr -d '='"
    ),
    helper="keyed.rsa",
    public_key=_RSA_3072_KEY,
    body=b'{"test": 3072}',
    headers=(
        ("X-Key-Id", "rsa-3072"),
        ("X-Timestamp", "32503680000456"),
        ("X-Delivery", "evt-10"),
        (
            "X-Signature",
            "v1="
            "PGXcg136-84tkDZtIzvbfI0eR_VBcwGC_dsULTTnC1YxhwP5bt4AkiX-fTI8B1ut84c1KJmN5dzl7ep_IIq-hswvJ_gK-MMv-TAH"
            "-VEKNcNdcquAYdSZRHUTEY3vQVJw5rAsZaZRfUqrtUZKVM8CSuk1qtlNSZ7H7yIS13lKz8K403VHndgIA1QC4GOUrp8VhFbEf4Yb"
            "As9MTx51UHR1ar9VJVJjHqcsP-hbV381nxbL0BGBthSSLSvy0-Tk9DUGnoo9kn6pGkbh2TmK28Xa4TnUZ4FCqrcUXWfh_pyIy-eZ"
            "dAi5BdI56rbHkJY3n0gKyTr5SpMK_C9KdAGN96iPVtxk2LPkPC2KmNhXqi7TAipc5UC6aF-VmN6dZGNbw78uA3hDarhetpgfqXYb"
            "UmugMYNOV4iw61tvCYWjhz7Q7tWmhlxpe88HEgWadPsta30SN4AOvNJfSYgqz1XxBtmIn8RKgP6Fxaa3_AstdOXgjZa3GWF5tsjo"
            "VEVLK9FSDxeF",
        ),
    ),
    now=KEYED.now.replace(microsecond=456000),
    key_id="rsa-3072",
)


class _Recording:
    """Record every call a key gets; verify answers with the next scripted error, or else with the wrapped real key."""

    def __init__(self, inner: object, *errors: BaseException) -> None:
        """Keep the real key, which may be None for a key that must never be used, and the errors to raise."""
        self.inner, self.errors, self.calls = inner, list(errors), list[str]()

    def called(self, name: str, *arguments: Any) -> Any:
        """Record a call and answer it."""
        self.calls.append(name)
        if name == "verify" and self.errors:
            raise self.errors.pop(0)
        return getattr(self.inner, name)(*arguments)

    def __repr__(self) -> str:
        """Record the call and show the marker, which no error or key representation may contain."""
        self.calls.append("__repr__")
        return _MARKER


def _recorded(name: str) -> Any:
    def method(self: _Recording, *arguments: Any) -> Any:
        return self.called(name, *arguments)

    return property(method) if name == "key_size" else method


def _recording(base: type) -> Any:
    """Return a subclass of a cryptography key class that records every one of its abstract methods."""
    namespace = {name: _recorded(name) for name in sorted(base.__abstractmethods__)}
    return type(base)(f"Recording{base.__name__}", (_Recording, base), namespace)


RecordingEd25519PublicKey: Final = _recording(Ed25519PublicKey)
RecordingRSAPublicKey: Final = _recording(RSAPublicKey)
RecordingEd25519PrivateKey: Final = _recording(Ed25519PrivateKey)
RecordingRSAPrivateKey: Final = _recording(RSAPrivateKey)


@dataclass(slots=True)
class PublicKeyWebhooks(Webhooks):
    """Call a generated package's webhook helpers with keys that wrap public keys, or HMAC keys for HMAC helpers."""

    def default_keys(self, vector: Any) -> Any:
        """Return the key set of a vector's own key."""
        if isinstance(vector, Vector):
            return Webhooks.default_keys(self, vector)
        return self.key_set((vector.key_id, vector.public_key))

    def key_set(self, *keys: tuple[str, Any]) -> Any:
        """Return a key set of Ed25519 or RSA-PSS keys, by the class of each public key, given with their ids."""
        return self.protocols.KeySet(keys=tuple(self.wrapped(name, key) for name, key in keys))

    def wrapped(self, name: str, key: object) -> Any:
        """Return the key wrapper of a public key, or an HMAC key for bytes."""
        if isinstance(key, bytes):
            return self.keys.HmacKey(id=name, secret=key)
        wrapper = self.keys.Ed25519Key if isinstance(key, Ed25519PublicKey) else self.keys.RSAPSSKey
        return wrapper(id=name, public_key=key)


def webhook_public_keys(package: ModuleType, lines: list[str]) -> None:
    """Verify Ed25519 and RSA-PSS vectors, rotation, key types, backend errors, imports, and secrecy."""
    hooks = PublicKeyWebhooks(package, lines)
    _imports(hooks)
    _vectors(hooks)
    _rejections(hooks)
    _candidates(hooks)
    _wrappers(hooks)
    _keys(hooks)
    _backend(hooks)
    _secrecy(hooks)


def _imports(hooks: PublicKeyWebhooks) -> None:
    """Load cryptography only with the key module or a public-key helper, whose hints then resolve."""
    package, lines = hooks.package, hooks.lines
    imports(package, lines, "standard.ed25519", (*PUBLIC_MODULES, ".webhooks.github.push", ".webhooks.keys"))
    imports(package, lines, "keyed.rsa", (".webhooks.wycheproof.rsa",))
    for name in ("Ed25519Key", "RSAPSSKey"):
        hints = get_type_hints(getattr(hooks.keys, name).__init__)
        lines.append(f"  {name} hints={ {key: getattr(value, '__name__', value) for key, value in hints.items()} }")
    lines.append(f"  key public names={hooks.keys.__all__}")


def _vectors(hooks: PublicKeyWebhooks) -> None:
    """Accept every published and authored vector; a body that is not JSON fails only after its signature verifies."""
    for vector in (*RFC_8032, *WYCHEPROOF_VALID, STANDARD_ED25519, RSA_2048, RSA_3072, GITHUB):
        hooks.check(vector.source, vector)


def _rejections(hooks: PublicKeyWebhooks) -> None:
    """Reject invalid published vectors, changed bodies and signed facts, and signatures of the wrong size."""
    for vector in WYCHEPROOF_INVALID:
        hooks.check(vector.source, vector)
    test_1, test_2 = RFC_8032
    signature = test_1.headers[0][1]
    hooks.check("RFC 8032 TEST 2 with the message 0x73", test_2, body=b"\x73")
    hooks.check(
        "RFC 8032 TEST 1 with the last signature byte changed from 0b to 0a",
        test_1,
        headers=test_1.changed("X-Signature", f"{signature[:-2]}0a"),
    )
    hooks.check("RFC 8032 TEST 1 signature of 63 bytes", test_1, headers=test_1.changed("X-Signature", signature[:-2]))
    hooks.check(
        "RFC 8032 TEST 1 signature of 65 bytes", test_1, headers=test_1.changed("X-Signature", f"{signature}00")
    )
    valid = WYCHEPROOF_VALID[1]
    hooks.check("Wycheproof tcId 4 with one changed body byte", valid, body=b"123401")
    hooks.check("Wycheproof tcId 4 with a trailing newline", valid, body=b"123400\n")
    hooks.check("Ed25519 changed body", STANDARD_ED25519, body=b'{"test": 25518}')
    hooks.check(
        "Ed25519 new timestamp", STANDARD_ED25519, headers=STANDARD_ED25519.changed("webhook-timestamp", "1614265331")
    )
    hooks.check(
        "Ed25519 new delivery id", STANDARD_ED25519, headers=STANDARD_ED25519.changed("webhook-id", "msg_ed2551")
    )
    hooks.check("RSA new delivery id", RSA_2048, headers=RSA_2048.changed("X-Delivery", "evt-8"))
    hooks.check("RSA changed body", RSA_2048, body=b'{"test": 2049}')
    hooks.check("RSA empty signature", RSA_2048, headers=RSA_2048.changed("X-Signature", ""))
    hooks.check("RSA prefix only", RSA_2048, headers=RSA_2048.changed("X-Signature", "v1="))


def _candidates(hooks: PublicKeyWebhooks) -> None:
    """Try keys in key-set order and signatures in header order, moving on only after an invalid signature."""
    signature = STANDARD_ED25519.headers[2][1]
    retired, active = ("retired", _RFC_8032_TEST_2_KEY), ("active", _ED25519_KEY)
    hooks.check("Ed25519 rotated to the new key", STANDARD_ED25519, keys=hooks.key_set(retired, active))
    hooks.check("Ed25519 both keys match", STANDARD_ED25519, keys=hooks.key_set(active, ("again", _ED25519_KEY)))
    hooks.check("Ed25519 only the retired key", STANDARD_ED25519, keys=hooks.key_set(retired))
    hooks.check("Ed25519 empty key set", STANDARD_ED25519, keys=hooks.key_set())
    hooks.check(
        "Ed25519 bad signature first",
        STANDARD_ED25519,
        headers=STANDARD_ED25519.changed("webhook-signature", f"{_ZERO_ED25519} {signature}"),
    )
    hooks.check(
        "Ed25519 signature header twice",
        STANDARD_ED25519,
        headers=(*STANDARD_ED25519.changed("webhook-signature", _ZERO_ED25519), ("Webhook-Signature", signature)),
    )
    sizes = hooks.key_set(("rsa-3072", _RSA_3072_KEY), ("wycheproof", _WYCHEPROOF_KEY))
    hooks.check("RSA keys of two sizes", WYCHEPROOF_VALID[1], keys=sizes)
    rotation = hooks.key_set(("rsa-2048", _RSA_2048_KEY), ("rsa-3072", _RSA_3072_KEY))
    hooks.check("RSA key id selects the 2048-bit key", RSA_2048, keys=rotation)
    hooks.check("RSA key id selects the 3072-bit key", RSA_3072, keys=rotation)
    hooks.check(
        "RSA key id names the other key", RSA_2048, keys=rotation, headers=RSA_2048.changed("X-Key-Id", "rsa-3072")
    )
    hooks.check("RSA unknown key id", RSA_2048, keys=rotation, headers=RSA_2048.changed("X-Key-Id", "rsa-4096"))
    both = f"{RSA_3072.headers[3][1]},{RSA_2048.headers[3][1]}"
    hooks.check(
        "RSA 3072-bit signature before the 2048-bit one",
        RSA_2048,
        keys=rotation,
        headers=RSA_2048.changed("X-Signature", both),
    )
    helper, store, keys = hooks.helper("keyed.rsa"), hooks.protocols.MemoryReplayStore(), hooks.default_keys(RSA_2048)
    for label in ("claimed", "duplicate"):
        claimed = outcome(
            lambda: helper.verify(RSA_2048.body, list(RSA_2048.headers), keys, now=RSA_2048.now, replay_store=store)
        )
        hooks.lines.append(f"  RSA {label} = {claimed}")


def _wrappers(hooks: PublicKeyWebhooks) -> None:
    """Refuse a key of another profile by its index in the key set, before verifying any signature."""
    valid = WYCHEPROOF_VALID[1]
    rsa, ed25519 = ("wycheproof", _WYCHEPROOF_KEY), ("ed25519", _ED25519_KEY)
    for label, vector, keys in (
        ("HMAC key for RSA-PSS", valid, hooks.key_set(("hmac", b"secret"))),
        ("Ed25519 key for RSA-PSS", valid, hooks.key_set(ed25519)),
        ("Ed25519 key after an RSA-PSS key", valid, hooks.key_set(rsa, ed25519)),
        ("RSA-PSS key for Ed25519", STANDARD_ED25519, hooks.key_set(rsa)),
        ("Ed25519 key for HMAC", GITHUB, hooks.key_set(ed25519)),
    ):
        hooks.check(label, vector, keys=keys)


def _keys(hooks: PublicKeyWebhooks) -> None:
    """Construct keys only from a valid id and a public key of their class, kept as given and never derived."""
    ed25519, rsa = hooks.keys.Ed25519Key, hooks.keys.RSAPSSKey
    private_ed25519, private_rsa = RecordingEd25519PrivateKey(None), RecordingRSAPrivateKey(None)
    der = _WYCHEPROOF_KEY.public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
    pem = _WYCHEPROOF_KEY.public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo).decode()
    for label, wrapper, arguments in (
        ("Ed25519 private key", ed25519, {"id": "key", "public_key": private_ed25519}),
        ("Ed25519 raw bytes", ed25519, {"id": "key", "public_key": _ED25519_KEY.public_bytes_raw()}),
        ("Ed25519 RSA key", ed25519, {"id": "key", "public_key": _WYCHEPROOF_KEY}),
        ("Ed25519 integer id", ed25519, {"id": 1, "public_key": _ED25519_KEY}),
        ("Ed25519 empty id", ed25519, {"id": "", "public_key": _ED25519_KEY}),
        ("Ed25519 id with LF", ed25519, {"id": "a\nb", "public_key": _ED25519_KEY}),
        ("RSA private key", rsa, {"id": "key", "public_key": private_rsa}),
        ("RSA DER bytes", rsa, {"id": "key", "public_key": der}),
        ("RSA PEM text", rsa, {"id": "key", "public_key": pem}),
        ("RSA Ed25519 key", rsa, {"id": "key", "public_key": _ED25519_KEY}),
        ("RSA id with NUL", rsa, {"id": "a\x00b", "public_key": _WYCHEPROOF_KEY}),
        ("RSA no public key", rsa, {"id": "key", "public_key": None}),
    ):
        record(hooks.lines, f"key {label}", lambda wrapper=wrapper, arguments=arguments: wrapper(**arguments))
    hooks.lines.append(f"  private keys asked for their public keys={private_ed25519.calls + private_rsa.calls}")
    for wrapper, public_key in ((ed25519, _ED25519_KEY), (rsa, _WYCHEPROOF_KEY)):
        name = wrapper.__name__
        hooks.lines.append(f"  {name} positional={failure(lambda w=wrapper, k=public_key: w('key', k))}")
        key = wrapper(id="active", public_key=public_key)
        record(
            hooks.lines,
            f"{name} id and the same public key",
            lambda key=key, k=public_key: (key.id, key.public_key is k),
        )
        record(hooks.lines, f"{name} assignment", lambda key=key: setattr(key, "id", "other"))
        record(hooks.lines, f"{name} deletion", lambda key=key: delattr(key, "public_key"))
        record(hooks.lines, f"{name} pickling", lambda key=key: pickle.dumps(key))
        record(hooks.lines, f"{name} representation", lambda key=key: repr(key))
        hooks.lines.append(f"  {name} copies are the key={copy.copy(key) is key and copy.deepcopy(key) is key}")


def _backend(hooks: PublicKeyWebhooks) -> None:
    """Use public keys only to verify; refuse a key its backend cannot use, and propagate any other error unchanged."""
    keys = (RecordingEd25519PublicKey(_ED25519_KEY), RecordingRSAPublicKey(_WYCHEPROOF_KEY))
    hooks.check("recorded Ed25519 key", STANDARD_ED25519, keys=hooks.key_set(("active", keys[0])))
    hooks.check("recorded RSA key", WYCHEPROOF_VALID[1], keys=hooks.key_set(("active", keys[1])))
    hooks.lines.append(f"  recorded calls={[key.calls for key in keys]}")
    for label, errors in (
        ("unsupported algorithm", (UnsupportedAlgorithm("scripted"), UnsupportedAlgorithm("scripted"))),
        ("invalid signature from a valid key", (InvalidSignature(), InvalidSignature())),
        ("programmer error", (RuntimeError("scripted"), RuntimeError("scripted"))),
    ):
        first = RecordingRSAPublicKey(_RSA_3072_KEY)
        scripted = RecordingRSAPublicKey(_WYCHEPROOF_KEY, *errors)
        last = RecordingRSAPublicKey(_WYCHEPROOF_KEY)
        keys = hooks.key_set(("first", first), ("scripted", scripted), ("last", last))
        hooks.check(label, WYCHEPROOF_VALID[1], keys=keys)
        hooks.lines.append(f"  {label} calls={[key.calls for key in (first, scripted, last)]}")
    scripted = RecordingRSAPublicKey(_RSA_2048_KEY, UnsupportedAlgorithm("scripted"), UnsupportedAlgorithm("scripted"))
    keys = hooks.key_set(("rsa-3072", RecordingRSAPublicKey(_RSA_3072_KEY)), ("rsa-2048", scripted))
    hooks.check("unsupported algorithm of the key the key id selects", RSA_2048, keys=keys)
    helper = hooks.helper("wycheproof.rsa")
    vector = WYCHEPROOF_VALID[1]
    for asynchronous in (False, True):
        cancelled = RecordingRSAPublicKey(_WYCHEPROOF_KEY, asyncio.CancelledError())
        last = RecordingRSAPublicKey(_WYCHEPROOF_KEY)
        keys = hooks.key_set(("cancelled", cancelled), ("last", last))
        arguments = (vector.body, list(vector.headers), keys)
        try:
            if asynchronous:
                asyncio.run(helper.verify_async(*arguments, now=vector.now))
            else:
                helper.verify(*arguments, now=vector.now)
        except asyncio.CancelledError:
            mode = " async" if asynchronous else ""
            hooks.lines.append(f"  cancelled verify{mode} propagated, calls {[cancelled.calls, last.calls]}")


def _secrecy(hooks: PublicKeyWebhooks) -> None:
    """Keep the marker public key out of keys, key sets, and everything a raised error reaches."""
    helper = hooks.helper("wycheproof.rsa")
    recorded = RecordingRSAPublicKey(_WYCHEPROOF_KEY, UnsupportedAlgorithm("scripted"))
    keys = hooks.key_set(("active", recorded))
    texts = [repr(keys.keys[0]), repr(keys)]
    signed, empty = WYCHEPROOF_VALID[1], WYCHEPROOF_VALID[0]
    for label, vector, body in (
        ("unsupported key", signed, signed.body),
        ("invalid signature", signed, b"123401"),
        ("empty event", empty, empty.body),
    ):
        try:
            helper.verify(body, list(vector.headers), keys, now=vector.now)
        except Exception as error:  # noqa: BLE001
            hooks.lines.append(f"  {label} ! {describe(error)} context={error.__context__!r}")
            texts.extend(reachable(error, set()))
    hooks.lines.append(f"  markers shown={[text for text in texts if _MARKER in text]} calls={recorded.calls}")
