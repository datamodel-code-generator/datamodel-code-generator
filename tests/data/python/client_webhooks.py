"""Verify generated webhook helpers against published signature vectors and vectors authored outside the runtime.

Published vectors come from the Standard Webhooks specification (whose example Svix documents), GitHub's and Slack's
webhook verification documentation, and RFC 4231 test cases 1, 2, and 6. Authored vectors were computed with OpenSSL
3.6 before the runtime existed; each vector's `source` names its origin or the exact command. Expected signatures are
literals, so no expected value comes from the code under test.
"""

from __future__ import annotations

import asyncio
import copy
import importlib
import json
import pickle
import subprocess
import sys
from base64 import b64decode
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

from tests.data.python.client_runtime import describe, record

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from types import ModuleType

_UTC: Final = timezone.utc
_MICROSECOND: Final = timedelta(microseconds=1)
_STANDARD_SECRET: Final = b64decode("MfKQ9r8GKYqrTwjUPD8ILPZIo2LaLaSw")
_ZERO_SIGNATURE: Final = "v1,AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
_IMPORT_PROBE: Final = """
import importlib
import json
import sys
from typing import get_type_hints
sys.path[:0] = sys.argv[1:3]
package, helper_name, *names = sys.argv[3:]
watched = ('verification', 'signatures', 'webhook_keys', 'public_keys')
def loaded():
    runtime = [name for name in watched if f'{package}._runtime.protocols.{name}' in sys.modules]
    return runtime + [name for name in ('cryptography',) if name in sys.modules]
for name in names:
    importlib.import_module(package + name)
    print(f'import {package}{name} loads {loaded()}')
helper = importlib.import_module(f'{package}.webhooks.{helper_name}')
print(f'helper loads {loaded()}')
optional = ('httpx2', 'httpcore2', 'cryptography')
print('helper optional imports=' + repr([name for name in optional if name in sys.modules]))
print('verify hints=' + repr(sorted(get_type_hints(helper.verify))))
print('verify_async hints=' + repr(sorted(get_type_hints(helper.verify_async))))
"""
PUBLIC_MODULES: Final = ("", ".protocols", ".errors", ".options", ".webhooks")


@dataclass(frozen=True, slots=True)
class Vector:
    """One signed delivery: where its signature comes from, the helper that verifies it, and its inputs."""

    source: str
    helper: str
    secret: bytes
    body: bytes
    headers: tuple[tuple[str, str], ...]
    now: datetime
    key_id: str = "active"

    def changed(self, name: str, value: str | None) -> tuple[tuple[str, str], ...]:
        """Return the headers with one header, by case-insensitive name, given a new value or removed."""
        kept = tuple(item for item in self.headers if item[0].lower() != name.lower())
        return kept if value is None else (*kept, (name, value))


STANDARD: Final = Vector(
    source="Standard Webhooks specification example, also in Svix's 'Verifying Webhooks Manually' documentation",
    helper="standard.message",
    secret=_STANDARD_SECRET,
    body=b'{"test": 2432232314}',
    headers=(
        ("webhook-id", "msg_p5jXN8AQM9LWM0D4loKWxJek"),
        ("webhook-timestamp", "1614265330"),
        ("webhook-signature", "v1,g0hM9SsE+OTPJTGt/tmIKtSyZlE3uFJELVlNIOLJ1OE="),
    ),
    now=datetime(2021, 2, 25, 15, 2, 10, tzinfo=_UTC),
)
GITHUB: Final = Vector(
    source="GitHub 'Validating webhook deliveries', the test values for verifying an implementation",
    helper="github.push",
    secret=b"It's a Secret to Everybody",
    body=b"Hello, World!",
    headers=(("X-Hub-Signature-256", "sha256=757107ea0eb2509fc211221cce984b8a37570b6d7586c22c46f4379c8b043e17"),),
    now=STANDARD.now,
)
RFC_4231: Final = tuple(
    Vector(
        source=f"RFC 4231 section 4 test case {case} HMAC-{digest.upper()}",
        helper=f"rfc.{digest}",
        secret=secret,
        body=data,
        headers=(("X-Signature", signature),),
        now=STANDARD.now,
    )
    for case, secret, data, digest, signature in (
        (1, b"\x0b" * 20, b"Hi There", "sha256", "b0344c61d8db38535ca8afceaf0bf12b881dc200c9833da726e9376c2e32cff7"),
        (
            1,
            b"\x0b" * 20,
            b"Hi There",
            "sha512",
            "87aa7cdea5ef619d4ff0b4241a1d6cb02379f4e2ce4ec2787ad0b30545e17cdedaa833b7d6b8a702038b274eaea3f4e4be9d914eeb6"
            "1f1702e696c203a126854",
        ),
        (
            2,
            b"Jefe",
            b"what do ya want for nothing?",
            "sha256",
            "5bdcc146bf60754e6a042426089575c75a003f089d2739839dec58b964ec3843",
        ),
        (
            2,
            b"Jefe",
            b"what do ya want for nothing?",
            "sha512",
            "164b7a7bfcf819e2e395fbe73b56e0a387bd64222e831fd610270cd7ea2505549758bf75c05a994a6d034f65f8f0e6fdcaeab1a34d4a"
            "6b4b636e070a38bce737",
        ),
        (
            6,
            b"\xaa" * 131,
            b"Test Using Larger Than Block-Size Key - Hash Key First",
            "sha256",
            "60e431591ee0b67f0d8a26aacbf5b77f8e0bc6213728c5140546040f0ee37f54",
        ),
        (
            6,
            b"\xaa" * 131,
            b"Test Using Larger Than Block-Size Key - Hash Key First",
            "sha512",
            "80b24263c7c1a3ebb71493c1dd7be8b49b46d1f41b4aeec1121b013783f8f3526b56d037e05f2598bd0fd2215d6a1e5295e64f73f63f0"
            "aec8b915a985d786598",
        ),
    )
)
YEAR_3000: Final = Vector(
    source=(
        "printf 'msg_replay3000.32503680000.{\"test\": 1}' | openssl dgst -sha256 -mac HMAC -macopt "
        "hexkey:31f290f6bf06298aab4f08d43c3f082cf648a362da2da4b0 -binary | openssl base64 -A"
    ),
    helper="standard.message",
    secret=_STANDARD_SECRET,
    body=b'{"test": 1}',
    headers=(
        ("webhook-id", "msg_replay3000"),
        ("webhook-timestamp", "32503680000"),
        ("webhook-signature", "v1,81k2hS4i/65v4JXKc7n2qQ2zazWerqrZE7nYDT6qdAY="),
    ),
    now=datetime(3000, 1, 1, tzinfo=_UTC),
)
LATEST: Final = Vector(
    source=(
        "printf 'msg_latest.253402300799.{\"test\": 9}' | openssl dgst -sha256 -mac HMAC -macopt "
        "hexkey:31f290f6bf06298aab4f08d43c3f082cf648a362da2da4b0 -binary | openssl base64 -A"
    ),
    helper="standard.message",
    secret=_STANDARD_SECRET,
    body=b'{"test": 9}',
    headers=(
        ("webhook-id", "msg_latest"),
        ("webhook-timestamp", "253402300799"),
        ("webhook-signature", "v1,Ul7cZCCDqGsyqLrQZROISv9d5c7YrmLiGucsLZd/Fm4="),
    ),
    now=datetime(9999, 12, 31, 23, 59, 59, tzinfo=_UTC),
)
CALLBACK: Final = Vector(
    source='printf \'{"id": "d-1"}\' | openssl dgst -sha256 -hmac callback-secret',
    helper="callbacks.delivered",
    secret=b"callback-secret",
    body=b'{"id": "d-1"}',
    headers=(("X-Signature", "78c1152c89eb1a3ff71601dd2637c4a2774f4622defcda26d93e383ad45e889c"),),
    now=STANDARD.now,
)
CIRCLE: Final = Vector(
    source="printf '{\"radius\": 1.5}' | openssl dgst -sha256 -hmac shape-secret",
    helper="shapes.drawn",
    secret=b"shape-secret",
    body=b'{"radius": 1.5}',
    headers=(("X-Signature", "752b358bc743212fb407d25fe1c7550d46728c90d7647fdc3d56bc962774f27b"),),
    now=STANDARD.now,
)
SQUARE: Final = replace(
    CIRCLE,
    source="printf '{\"side\": 2}' | openssl dgst -sha256 -hmac shape-secret",
    body=b'{"side": 2}',
    headers=(("X-Signature", "32afebc8db7b37d89e2349eafd7fc537599977f9aa05539df9ab55bcc9adbb63"),),
)
MARKER: Final = Vector(
    source=(
        'printf \'msg_marker.1614265330.{"test": "MARKER-BODY"}\' | openssl dgst -sha256 -hmac MARKER-SECRET '
        "-binary | openssl base64 -A"
    ),
    helper="standard.message",
    secret=b"MARKER-SECRET",
    body=b'{"test": "MARKER-BODY"}',
    headers=(
        ("webhook-id", "msg_marker"),
        ("webhook-timestamp", "1614265330"),
        ("webhook-signature", "v1,Idi/sRamClJH8JNUPkuddpTLIt0jCvdw538K24paUDg="),
    ),
    now=STANDARD.now,
)

MARKER_JSON: Final = replace(
    MARKER,
    source=(
        "printf 'msg_marker.1614265330.{\"test\": MARKER-BODY' | openssl dgst -sha256 -hmac MARKER-SECRET -binary | "
        "openssl base64 -A"
    ),
    body=b'{"test": MARKER-BODY',
    headers=MARKER.changed("webhook-signature", "v1,QdIJaF5wHYASs9n88VWRsYYY8SnldHlAgMPW/kGbZrk="),
)
LAST_BYTE_CHANGED: Final = (
    replace(
        RFC_4231[0],
        source="RFC 4231 test case 1 HMAC-SHA256 with the last signature byte changed from f7 to f6",
        headers=(("X-Signature", "b0344c61d8db38535ca8afceaf0bf12b881dc200c9833da726e9376c2e32cff6"),),
    ),
    replace(
        RFC_4231[1],
        source="RFC 4231 test case 1 HMAC-SHA512 with the last signature byte changed from 54 to 55",
        headers=(
            (
                "X-Signature",
                "87aa7cdea5ef619d4ff0b4241a1d6cb02379f4e2ce4ec2787ad0b30545e17cdedaa833b7d6b8a702038b274eaea3f4e4be9d914e"
                "eb61f1702e696c203a126855",
            ),
        ),
    ),
)


def shown(result: Any) -> str:
    """Describe a verified webhook: its event type and value, then every signature fact."""
    stamp = None if result.timestamp is None else result.timestamp.isoformat()
    return (
        f"{type(result.data).__name__} {result.data!r} delivery_id={result.delivery_id!r} timestamp={stamp} "
        f"matched_key_id={result.matched_key_id!r}"
    )


def failure(call: Callable[[], object]) -> str:
    """Name the class of a call's failure, whose message Python words differently across versions."""
    try:
        call()
    except Exception as error:  # ruff: ignore[blind-except]
        return type(error).__name__
    return "none"


def outcome(call: Callable[[], Any]) -> str:
    """Describe a verification's result or its failure."""
    try:
        result = call()
    except Exception as error:  # ruff: ignore[blind-except]
        return describe(error)
    return shown(result)


@dataclass(slots=True)
class Webhooks:
    """Call one generated package's webhook helpers, reporting each delivery once when both modes agree."""

    package: ModuleType
    lines: list[str]
    protocols: ModuleType = field(init=False)
    keys: ModuleType = field(init=False)

    def __post_init__(self) -> None:
        """Import the shared contracts and the key types."""
        self.protocols = importlib.import_module(f"{self.package.__name__}.protocols")
        self.keys = importlib.import_module(f"{self.package.__name__}.webhooks.keys")

    def default_keys(self, vector: Any) -> Any:
        """Return the key set of a vector's own key."""
        return self.key_set((vector.key_id, vector.secret))

    def helper(self, name: str) -> ModuleType:
        """Import a helper module by its dotted name."""
        return importlib.import_module(f"{self.package.__name__}.webhooks.{name}")

    def key_set(self, *keys: tuple[str, bytes]) -> Any:
        """Return a key set of HMAC keys given as id and secret pairs."""
        return self.protocols.KeySet(keys=tuple(self.keys.HmacKey(id=name, secret=secret) for name, secret in keys))

    def check(
        self,
        label: str,
        vector: Vector,
        *,
        body: object = None,
        headers: object = None,
        keys: object = None,
        now: object = None,
        **options: Any,
    ) -> None:
        """Verify a delivery with verify and verify_async, reporting one outcome, or both when they differ."""
        helper = self.helper(vector.helper)
        arguments = (
            vector.body if body is None else body,
            list(vector.headers) if headers is None else headers,
            self.default_keys(vector) if keys is None else keys,
        )
        moment = vector.now if now is None else now
        sync = outcome(lambda: helper.verify(*arguments, now=moment, **options))
        asynchronous = outcome(lambda: asyncio.run(helper.verify_async(*arguments, now=moment, **options)))
        self.lines.append(f"  {label} = {sync}")
        if asynchronous != sync:
            self.lines.append(f"  {label} async = {asynchronous}")


def webhook_verification(package: ModuleType, lines: list[str]) -> None:
    """Verify published and authored vectors, every rejection and boundary, limits, configuration, and secrecy."""
    hooks = Webhooks(package, lines)
    _imports(package, lines)
    _vectors(hooks)
    _rejections(hooks)
    _windows(hooks)
    _candidates(hooks)
    _syntax(hooks)
    _limits(hooks)
    _configuration(hooks)
    _keys(hooks)
    _secrecy(hooks)


def _imports(package: ModuleType, lines: list[str]) -> None:
    """Import the package and its public modules in a fresh process, then one helper and its type hints."""
    imports(package, lines, "standard.message", (*PUBLIC_MODULES, ".webhooks.keys"))


def imports(package: ModuleType, lines: list[str], helper: str, modules: tuple[str, ...]) -> None:
    """Import modules of a package in order in a fresh process, reporting what each loads, then a helper's hints."""
    if (location := package.__file__) is None:
        msg = "Generated package has no source path"
        raise RuntimeError(msg)
    source = Path(location).parent.parent
    models_file = f"{package.__name__}_models.py"
    models = next(parent for parent in (source, *source.parents) if (parent / models_file).is_file())
    completed = subprocess.run(
        [sys.executable, "-I", "-c", _IMPORT_PROBE, str(models), str(source), package.__name__, helper, *modules],
        check=True,
        capture_output=True,
        text=True,
    )
    lines.extend(f"  {line.replace(package.__name__, '<package>')}" for line in completed.stdout.splitlines())


def _vectors(hooks: Webhooks) -> None:
    """Accept every published and authored vector; a body that is not JSON fails only after its signature verifies."""
    for vector in (STANDARD, GITHUB, *RFC_4231, YEAR_3000, LATEST, CALLBACK, CIRCLE, SQUARE, *_presets()):
        hooks.check(vector.source, vector)


def _rejections(hooks: Webhooks) -> None:
    """Reject changed bodies and changed signed facts under an unchanged signature, and an ambiguous framing."""
    for vector in _presets():
        hooks.check("Stripe changed body", vector, body=vector.body + b"\n")
        signature = vector.headers[-1][1]
        for label, header in (
            ("Stripe repeated timestamp", signature + ",t=1614265330"),
            ("Stripe no timestamp", signature.split(",", 1)[1]),
            ("Stripe no signature", "t=1614265330"),
            ("Stripe nondecimal timestamp", signature.replace("1614265330", "+1614265330")),
            ("Stripe other version", signature.replace("v1=", "v0=")),
            ("Stripe rotated signatures", signature + ",v1=" + "00" * 32),
        ):
            hooks.check(label, vector, headers=vector.changed("Stripe-Signature", header))
        hooks.check("Stripe expired", vector, now=vector.now + timedelta(seconds=301))
        hooks.check("Stripe repeated independently", vector)
    hooks.check("one changed body byte", STANDARD, body=b'{"test": 2432232315}')
    hooks.check("trailing newline", STANDARD, body=STANDARD.body + b"\n")
    hooks.check("new timestamp", STANDARD, headers=STANDARD.changed("webhook-timestamp", "1614265331"))
    hooks.check("new delivery id", STANDARD, headers=STANDARD.changed("webhook-id", "msg_p5jXN8AQM9LWM0D4loKWxJel"))
    hooks.check("GitHub changed body", GITHUB, body=b"Hello, World?")
    for vector in LAST_BYTE_CHANGED:
        hooks.check(vector.source, vector)


def _windows(hooks: Webhooks) -> None:
    """Accept both inclusive timestamp boundaries and refuse one microsecond beyond, with exact tolerances."""
    stamp = STANDARD.now
    for label, now, options in (
        ("300 seconds past", stamp + timedelta(seconds=300), {}),
        ("past bound exceeded", stamp + timedelta(seconds=300) + _MICROSECOND, {}),
        ("30 seconds future", stamp - timedelta(seconds=30), {}),
        ("future bound exceeded", stamp - timedelta(seconds=30) - _MICROSECOND, {}),
        ("zero past tolerance", stamp, {"past_tolerance": 0}),
        ("zero past tolerance exceeded", stamp + _MICROSECOND, {"past_tolerance": 0}),
        ("zero future tolerance exceeded", stamp - _MICROSECOND, {"future_tolerance": 0.0}),
        ("fractional tolerance", stamp + timedelta(seconds=0.5), {"past_tolerance": 0.5}),
        ("fractional tolerance exceeded", stamp + timedelta(seconds=0.5) + _MICROSECOND, {"past_tolerance": 0.5}),
        ("offset clock", stamp.astimezone(timezone(timedelta(hours=9))), {}),
    ):
        options = {"options": hooks.protocols.WebhookOptions(**options)} if options else {}
        hooks.check(label, STANDARD, now=now, **options)
    beyond = STANDARD.changed("webhook-timestamp", "253402300800")
    hooks.check("timestamp after datetime.max", STANDARD, headers=beyond, now=datetime.max.replace(tzinfo=_UTC))
    hooks.check("earliest clock", STANDARD, now=datetime.min.replace(tzinfo=_UTC))


def _candidates(hooks: Webhooks) -> None:
    """Try keys in tuple order and signatures in header order, filtered by a key-id header."""
    signature = STANDARD.headers[2][1]
    retired, active = ("retired", b"retired secret"), ("active", STANDARD.secret)
    hooks.check(
        "bad signature first", STANDARD, headers=STANDARD.changed("webhook-signature", f"{_ZERO_SIGNATURE} {signature}")
    )
    hooks.check(
        "signature header twice",
        STANDARD,
        headers=(*STANDARD.changed("webhook-signature", _ZERO_SIGNATURE), ("Webhook-Signature", signature)),
    )
    hooks.check("rotated to the new key", STANDARD, keys=hooks.key_set(retired, active))
    both = hooks.key_set(("first", STANDARD.secret), ("second", STANDARD.secret))
    hooks.check("both keys match", STANDARD, keys=both)
    hooks.check("only the retired key", STANDARD, keys=hooks.key_set(retired))
    hooks.check("empty key set", STANDARD, keys=hooks.key_set())
    upper = f"sha256={GITHUB.headers[0][1].removeprefix('sha256=').upper()}"
    hooks.check("uppercase hex", GITHUB, headers=GITHUB.changed("x-hub-signature-256", upper))


def _syntax(hooks: Webhooks) -> None:
    """Refuse malformed signatures and facts without trying any key, and order failures by stage."""
    signature = STANDARD.headers[2][1]
    for label, name, value in (
        ("other version prefix", "webhook-signature", f"v1a,{signature[3:]} {signature}"),
        ("unpadded base64", "webhook-signature", signature.rstrip("=")),
        ("short signature", "webhook-signature", "v1,AAAA"),
        ("empty element", "webhook-signature", f"{signature}  {signature}"),
        ("prefix only", "webhook-signature", "v1,"),
        ("no signature", "webhook-signature", None),
        ("hexadecimal timestamp", "webhook-timestamp", "0x10"),
        ("signed timestamp", "webhook-timestamp", "+1614265330"),
        ("spaced timestamp", "webhook-timestamp", " 1614265330"),
        ("twenty digit timestamp", "webhook-timestamp", "1" * 20),
        ("no timestamp", "webhook-timestamp", None),
        ("delivery id with obs-text", "webhook-id", "msg_é"),
        ("no delivery id", "webhook-id", None),
    ):
        hooks.check(label, STANDARD, headers=STANDARD.changed(name, value))
    hooks.check("repeated timestamp", STANDARD, headers=(*STANDARD.headers, ("Webhook-Timestamp", "1614265330")))
    hooks.check("odd hex", GITHUB, headers=GITHUB.changed("X-Hub-Signature-256", GITHUB.headers[0][1][:-1]))
    far = STANDARD.now + timedelta(days=1)
    hooks.check("malformed before window", STANDARD, now=far, headers=STANDARD.changed("webhook-signature", "v1,A"))
    zero = STANDARD.changed("webhook-signature", _ZERO_SIGNATURE)
    hooks.check("window before signature", STANDARD, now=far, headers=zero)


def _limits(hooks: Webhooks) -> None:
    """Refuse oversized bodies, headers, key sets, and signature lists before decoding a signature."""
    options = hooks.protocols.WebhookOptions
    many = " ".join([_ZERO_SIGNATURE] * 8 + [STANDARD.headers[2][1]])
    hooks.check("nine signatures", STANDARD, headers=STANDARD.changed("webhook-signature", many))
    hooks.check("nine malformed signatures", STANDARD, headers=STANDARD.changed("webhook-signature", "x " * 8 + "x"))
    nine = hooks.key_set(*((f"key-{index}", STANDARD.secret) for index in range(9)))
    hooks.check("nine keys", STANDARD, keys=nine)
    hooks.check("nine keys allowed", STANDARD, keys=nine, options=options(max_keys=9))
    hooks.check("body limit", STANDARD, options=options(max_body_bytes=len(STANDARD.body)))
    hooks.check("body over limit", STANDARD, options=options(max_body_bytes=len(STANDARD.body) - 1))
    octets = sum(len(name) + len(value) for name, value in STANDARD.headers)
    hooks.check("header limit", STANDARD, options=options(max_header_bytes=octets))
    hooks.check("headers over limit", STANDARD, options=options(max_header_bytes=octets - 1))
    hooks.check("one signature allowed", STANDARD, options=options(max_signatures=1))


def _configuration(hooks: Webhooks) -> None:
    """Refuse every argument of the wrong type or value with its field path, before any other stage."""
    for label, changes in (
        ("text body", {"body": STANDARD.body.decode()}),
        ("bytearray body", {"body": bytearray(STANDARD.body)}),
        ("header mapping", {"headers": dict(STANDARD.headers)}),
        ("header lists", {"headers": [list(item) for item in STANDARD.headers]}),
        ("header bytes", {"headers": [(name, value.encode()) for name, value in STANDARD.headers]}),
        ("header triple", {"headers": [(*STANDARD.headers[0], "extra")]}),
        ("key tuple", {"keys": (hooks.keys.HmacKey(id="active", secret=STANDARD.secret),)}),
        ("naive now", {"now": STANDARD.now.replace(tzinfo=None)}),
        ("text now", {"now": "2021-02-25T15:02:10Z"}),
        ("text body and naive now", {"body": "{}", "now": STANDARD.now.replace(tzinfo=None)}),
        ("options mapping", {"options": {"max_keys": 1}}),
        ("foreign key", {"keys": hooks.protocols.KeySet(keys=(object(),))}),
        ("repeated key id", {"keys": hooks.key_set(("active", b"one"), ("active", STANDARD.secret))}),
    ):
        hooks.check(label, STANDARD, **changes)


def _keys(hooks: Webhooks) -> None:
    """Construct HMAC keys only from a valid id and exact bytes, and keep their secrets out of every copy."""
    key_type = hooks.keys.HmacKey
    for label, arguments in (
        ("integer id", {"id": 1, "secret": b"secret"}),
        ("empty id", {"id": "", "secret": b"secret"}),
        ("id with NUL", {"id": "a\x00b", "secret": b"secret"}),
        ("id with CR", {"id": "a\rb", "secret": b"secret"}),
        ("id with LF", {"id": "a\nb", "secret": b"secret"}),
        ("bytearray secret", {"id": "key", "secret": bytearray(b"secret")}),
        ("text secret", {"id": "key", "secret": "secret"}),
        ("empty secret", {"id": "key", "secret": b""}),
    ):
        record(hooks.lines, f"key {label}", lambda arguments=arguments: key_type(**arguments))
    hooks.lines.append(f"  key positional={failure(lambda: key_type('key', b'secret'))}")
    key = key_type(id="active", secret=b"MARKER-SECRET")
    record(hooks.lines, "key id and secret", lambda: (key.id, len(key.secret)))
    record(hooks.lines, "key assignment", lambda: setattr(key, "id", "other"))
    record(hooks.lines, "key deletion", lambda: delattr(key, "id"))
    record(hooks.lines, "key pickling", lambda: pickle.dumps(key))
    hooks.lines.append(f"  key copies are the key={copy.copy(key) is key and copy.deepcopy(key) is key}")
    hooks.lines.append(f"  key public names={hooks.keys.__all__}")


def reachable(value: object, seen: set[int]) -> Iterator[str]:
    """Yield the text of a value and of everything an error reaches: its causes, context, arguments, and attributes."""
    if id(value) in seen:
        return
    seen.add(id(value))
    yield repr(value)
    if isinstance(value, BaseException):
        yield str(value)
        reached = (
            getattr(value, "cause", None),
            value.__cause__,
            value.__context__,
            *value.args,
            *vars(value).values(),
        )
        for item in reached:
            yield from reachable(item, seen)


def _secrecy(hooks: Webhooks) -> None:
    """Keep the marker secret, body, and signature out of keys and out of everything a raised error reaches."""
    helper = hooks.helper("standard.message")
    keys = hooks.key_set(("active", MARKER.secret))
    texts = [repr(keys.keys[0]), repr(keys)]
    for label, vector, headers in (
        ("marker event", MARKER, list(MARKER.headers)),
        ("marker JSON", MARKER_JSON, list(MARKER_JSON.headers)),
        ("marker signature", MARKER, MARKER.changed("webhook-signature", "v1,MARKER-SIGNATURE")),
        ("marker key", MARKER, MARKER.changed("webhook-signature", _ZERO_SIGNATURE)),
    ):
        try:
            helper.verify(vector.body, headers, keys, now=vector.now)
        except Exception as error:  # ruff: ignore[blind-except]
            hooks.lines.append(f"  {label} ! {describe(error)} context={error.__context__!r}")
            texts.extend(reachable(error, set()))
    hooks.lines.append(f"  markers shown={[text for text in texts if 'MARKER' in text]}")


def webhook_backends(package: ModuleType, lines: list[str]) -> None:
    """Decode verified events into each backend's types, schema-validating unions only their schemas tell apart."""
    hooks = Webhooks(package, lines)
    for vector in (STANDARD, CALLBACK, CIRCLE, MARKER):
        hooks.check(vector.helper, vector)


_UNAWAITED: Final = object()


def _presets() -> tuple[Vector, ...]:
    """Read fixed preset deliveries copied from the existing independent adapter vectors."""
    path = Path(__file__).parents[1] / "generation_platform/client/webhook-presets.json"
    return tuple(
        Vector(
            source=item["source"],
            helper=item["helper"],
            secret=bytes.fromhex(item["secret_hex"]),
            body=item["body"].encode(),
            headers=tuple(tuple(pair) for pair in item["headers"]),
            now=datetime.fromisoformat(item["now"]),
            key_id=item["key_id"],
        )
        for item in json.loads(path.read_text())
    )
