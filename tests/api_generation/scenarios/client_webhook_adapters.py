"""Verify generated adapter webhook helpers through application verifiers, and decode unsigned and mapped events.

The verifiers below are test adapters written from each scheme's public documentation, independently of the runtime:
a Stripe-style `Stripe-Signature: t=<seconds>,v1=<hex>` header signing `<t>.<raw body>`, the Standard Webhooks headers,
and GitHub's `X-Hub-Signature-256`. The Standard Webhooks and GitHub deliveries are their published examples, as in
client_webhooks; the other vectors were computed with OpenSSL 3.0.2 by the command each `source` names. No expected
value comes from the code under test.
"""

from __future__ import annotations

import asyncio
import gc
import hmac
import importlib
import subprocess
import sys
import warnings
from base64 import b64decode
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta, timezone, tzinfo
from itertools import starmap
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

from tests.api_generation.scenarios.client_webhooks import (
    GITHUB,
    STANDARD,
    Vector,
    failure,
    reachable,
    shown,
)
from tests.api_generation.support.client_runtime import describe, record

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import ModuleType

_UTC: Final = timezone.utc
_MICROSECOND: Final = timedelta(microseconds=1)
_STAMP: Final = datetime(2021, 2, 25, 15, 2, 10, tzinfo=_UTC)
_STRIPE_SECRET: Final = b"whsec_adapter_secret"
_RETIRED_SECRET: Final = b"whsec_retired_secret"
_INVOICE: Final = b'{"type": "invoice.paid", "id": "in_1", "amount": 1200}'
_IMPORT_PROBE: Final = """
import importlib
import sys
from typing import get_type_hints
sys.path[:0] = sys.argv[1:3]
package, *names = sys.argv[3:]
watched = ('webhook_events', 'adapters', 'verification', 'signatures', 'webhook_keys', 'public_keys')
def binds_hmac():
    # Pydantic 2.14 loads hmac itself, so look for a package module binding hmac or one of its functions.
    hmac = sys.modules.get('hmac')
    modules = [module for key, module in list(sys.modules.items()) if key.startswith(package + '.') and module]
    if hmac is None:
        return False
    exported = {id(hmac), *(id(getattr(hmac, name)) for name in ('new', 'digest', 'compare_digest', 'HMAC'))}
    return any(id(value) in exported for module in modules for value in vars(module).values())
def loaded():
    runtime = [name for name in watched if f'{package}._runtime.protocols.{name}' in sys.modules]
    return runtime + ['hmac'] * binds_hmac() + [name for name in ('cryptography',) if name in sys.modules]
for name in names:
    try:
        module = importlib.import_module(package + name)
    except ImportError as error:
        print(f'import {package}{name} ! {type(error).__name__}')
        continue
    print(f'import {package}{name} loads {loaded()}')
    for function in getattr(module, '__all__', ()) if name.count('.') > 2 else ():
        print(f'  {function} hints={sorted(get_type_hints(getattr(module, function)))}')
"""


def _stripe(source: str, body: bytes, signatures: str, helper: str = "stripe.event") -> Vector:
    return Vector(
        source=source,
        helper=helper,
        secret=_STRIPE_SECRET,
        body=body,
        headers=(("Content-Type", "application/json"), ("Stripe-Signature", f"t=1614265330,{signatures}")),
        now=_STAMP,
        key_id="stripe-2026",
    )


def _openssl(body: bytes, secret: bytes = _STRIPE_SECRET) -> str:
    return f"printf '1614265330.{body.decode()}' | openssl dgst -sha256 -hmac {secret.decode()}"


STRIPE_INVOICE: Final = _stripe(
    _openssl(_INVOICE), _INVOICE, "v1=dc518e47589d1bfd353029d070e8cd3f2896fdfafd829e64996fcd51b6c6de7b"
)
_CUSTOMER: Final = b'{"type": "customer.created", "id": "cus_1", "email": "a@example.com"}'
STRIPE_CUSTOMER: Final = _stripe(
    _openssl(_CUSTOMER), _CUSTOMER, "v1=10760a38fe38772edceab1175cdc7ab20aa164eb2c58366ba9849cdb75761150"
)
_UPDATED: Final = b'{"type": "invoice.updated", "id": "in_1", "amount": 1500}'
STRIPE_UPDATED: Final = _stripe(
    f"{_openssl(_UPDATED, _RETIRED_SECRET)}, then {_openssl(_UPDATED)}",
    _UPDATED,
    "v1=63bc296a0223a8c53783ae6ab49a3e15d7bf39284dd616b8ad3616bdeef8daab,"
    "v1=d32791b252265a423941d1432f00bf3f323c4b06a8907e32a23ca8fc677b8e1b",
)
_VOIDED: Final = b'{"type": "invoice.voided", "id": "in_2", "amount": 1}'
STRIPE_VOIDED: Final = _stripe(
    _openssl(_VOIDED), _VOIDED, "v1=23f41003b15a406b8798fd3397865f6910aba614a22f00379eb6247d6bb99fc5"
)
STANDARD_ADAPTED: Final = replace(STANDARD, helper="adapted.message")
GITHUB_HELLO: Final = replace(GITHUB, helper="adapted.plain")
GITHUB_MESSAGE: Final = replace(
    GITHUB,
    source='printf \'{"test": 5}\' | openssl dgst -sha256 -hmac "It\'s a Secret to Everybody"',
    helper="adapted.plain",
    body=b'{"test": 5}',
    headers=(("X-Hub-Signature-256", "sha256=e5b8beef9bf816de0157e5f3506fe3dc357e4f193b0cba41d5f87608b97c90e3"),),
)
MAPPED: Final = tuple(
    Vector(
        source=f"printf '{body.decode()}' | openssl dgst -sha256 -hmac mapped-secret",
        helper="mapped.event",
        secret=b"mapped-secret",
        body=body,
        headers=(("X-Signature", signature),),
        now=_STAMP,
    )
    for body, signature in (
        (_INVOICE, "2afa46cbc373bd51e5b7fe70d03b7bf7e15399f3b45b58d30c6b79f30e46501a"),
        (_CUSTOMER, "2339078ebf6bb5f6ee4efff40f4820e7746675a6cd65090831039f5d4920af8f"),
        (_VOIDED, "0e21b8b3cea4c4934c910e5b8b0e9e9985c9197e16b67a1088140a8a5122a7c3"),
    )
)


@dataclass(frozen=True)
class AppKey:
    """An application-owned key; the runtime never reads its fields."""

    name: str
    secret: bytes = field(repr=False)


class OpaqueKey:
    """A key that records every attribute read and representation, to show the runtime never inspects keys."""

    def __init__(self, reads: list[str]) -> None:
        """Keep the list each read is recorded in."""
        object.__setattr__(self, "_reads", reads)

    def __getattribute__(self, name: str) -> Any:
        """Record a read of any attribute but the list itself."""
        if name != "_reads":
            object.__getattribute__(self, "_reads").append(name)
        return object.__getattribute__(self, name)

    def __repr__(self) -> str:
        """Record a representation."""
        object.__getattribute__(self, "_reads").append("__repr__")
        return "OpaqueKey()"


@dataclass(slots=True)
class Contracts:
    """The generated package's public modules a test verifier builds its results and errors from."""

    protocols: ModuleType
    errors: ModuleType

    def malformed(self) -> Exception:
        """Return the error of a signature header the verifier cannot read."""
        return self.errors.ProtocolDataError(reason="malformed_signature")

    def rejection(self, condition: str) -> Exception:
        """Return a verification error of the condition."""
        return self.errors.ProtocolDataError(reason=condition)

    def matched(self, signed: bytes, signatures: list[bytes], keys: Any, limits: Any) -> str:
        """Return the name of the first key, in key order, whose HMAC-SHA256 of the signed bytes is a signature."""
        if len(signatures) > limits.max_signatures:
            raise self.errors.ProtocolDataError(reason="too_large")
        if not keys.keys:
            msg = "missing_key"
            raise self.rejection(msg)
        for key in keys.keys:
            expected = hmac.new(key.secret, signed, "sha256").digest()
            if any(hmac.compare_digest(expected, signature) for signature in signatures):
                return key.name
        msg = "invalid_signature"
        raise self.rejection(msg)


class StripeVerifier(Contracts):
    """Verify `Stripe-Signature: t=<seconds>,v1=<hex>[,v1=<hex>...]` over `<t>.<raw body>`; skip other schemes."""

    def verify(self, raw_body: bytes, ordered_headers: Any, keys: Any, now: datetime, limits: Any) -> Any:
        """Return the timestamp and the matched key, or raise the scheme's verification error."""
        del now
        values = [value for name, value in ordered_headers if name.lower() == "stripe-signature"]
        if len(values) != 1:
            raise self.malformed()
        stamp, signatures = None, []
        for item in values[0].split(","):
            scheme, separator, value = item.partition("=")
            if not separator or (scheme == "t" and (stamp is not None or not value.isdigit() or not value.isascii())):
                raise self.malformed()
            if scheme == "t":
                stamp = value
            elif scheme == "v1":
                try:
                    signatures.append(bytes.fromhex(value))
                except ValueError:
                    raise self.malformed() from None
        if stamp is None or not signatures:
            raise self.malformed()
        name = self.matched(stamp.encode() + b"." + raw_body, signatures, keys, limits)
        moment = datetime.fromtimestamp(int(stamp), _UTC)
        return self.protocols.VerifiedSignature(delivery_id=None, timestamp=moment, matched_key_id=name)


class StandardVerifier(Contracts):
    """Verify the Standard Webhooks headers: `v1,<base64>` signatures over `<webhook-id>.<webhook-timestamp>.<body>`."""

    def verify(self, raw_body: bytes, ordered_headers: Any, keys: Any, now: datetime, limits: Any) -> Any:
        """Return the delivery id, the timestamp, and the matched key, or raise the scheme's verification error."""
        del now
        found: dict[str, list[str]] = {}
        for name, value in ordered_headers:
            found.setdefault(name.lower(), []).append(value)
        identity, stamp, signature = (
            found.get(name, []) for name in ("webhook-id", "webhook-timestamp", "webhook-signature")
        )
        if len(identity) != 1 or len(stamp) != 1 or len(signature) != 1 or "." in identity[0] or not stamp[0].isdigit():
            raise self.malformed()
        signatures = [b64decode(item[3:], validate=True) for item in signature[0].split(" ") if item.startswith("v1,")]
        name = self.matched(f"{identity[0]}.{stamp[0]}.".encode() + raw_body, signatures, keys, limits)
        moment = datetime.fromtimestamp(int(stamp[0]), _UTC)
        return self.protocols.VerifiedSignature(delivery_id=identity[0], timestamp=moment, matched_key_id=name)


class GithubVerifier(Contracts):
    """Verify GitHub's `X-Hub-Signature-256: sha256=<hex>` over the raw body alone, which signs no other fact."""

    def verify(self, raw_body: bytes, ordered_headers: Any, keys: Any, now: datetime, limits: Any) -> Any:
        """Return the matched key without facts, or raise the scheme's verification error."""
        del now
        values = [value for name, value in ordered_headers if name.lower() == "x-hub-signature-256"]
        if len(values) != 1 or not values[0].startswith("sha256="):
            raise self.malformed()
        name = self.matched(raw_body, [bytes.fromhex(values[0][7:])], keys, limits)
        return self.protocols.VerifiedSignature(delivery_id=None, timestamp=None, matched_key_id=name)


class Scripted:
    """A verifier that answers each call with its scripted result, or raises it, and records how it was called."""

    def __init__(self, answer: object) -> None:
        """Keep the answer."""
        self.answer = answer
        self.calls: list[tuple[object, ...]] = []

    def verify(self, raw_body: bytes, ordered_headers: Any, keys: Any, now: datetime, limits: Any) -> Any:
        """Record the call and give the answer."""
        self.calls.append((raw_body, ordered_headers, keys, now, limits))
        if isinstance(self.answer, BaseException):
            raise self.answer
        return self.answer


class AsyncVerifier:
    """A verifier whose verify is a coroutine function, so its result is an unawaited coroutine."""

    async def verify(self, raw_body: bytes, ordered_headers: Any, keys: Any, now: datetime, limits: Any) -> Any:
        """Never run: the helper closes the coroutine."""
        del raw_body, ordered_headers, keys, now, limits
        return None


class Recording:
    """Wrap a verifier, recording each call's arguments."""

    def __init__(self, inner: Any) -> None:
        """Keep the wrapped verifier."""
        self.inner = inner
        self.calls: list[tuple[object, ...]] = []

    def verify(self, raw_body: bytes, ordered_headers: Any, keys: Any, now: datetime, limits: Any) -> Any:
        """Record the call and verify with the wrapped verifier."""
        self.calls.append((raw_body, ordered_headers, keys, now, limits))
        return self.inner.verify(raw_body, ordered_headers, keys, now, limits)


def outcome(call: Callable[[], Any]) -> str:
    """Describe a verification's result, or its failure with its delivery state when it has one."""
    try:
        result = call()
    except Exception as error:  # ruff: ignore[blind-except]
        return describe(error)
    return shown(result)


@dataclass(slots=True)
class Adapters:
    """Call one generated package's adapter helpers, reporting each delivery once when both modes agree."""

    package: ModuleType
    lines: list[str]
    protocols: ModuleType = field(init=False)
    errors: ModuleType = field(init=False)

    def __post_init__(self) -> None:
        """Import the shared contracts and errors."""
        self.protocols = importlib.import_module(f"{self.package.__name__}.protocols")
        self.errors = importlib.import_module(f"{self.package.__name__}.errors")

    def helper(self, name: str) -> ModuleType:
        """Import a helper module by its dotted name."""
        return importlib.import_module(f"{self.package.__name__}.webhooks.{name}")

    def verifier(self, kind: type[Contracts]) -> Any:
        """Return a test verifier built from this package's contracts."""
        return kind(self.protocols, self.errors)

    def key_set(self, *keys: tuple[str, bytes]) -> Any:
        """Return a key set of application keys given as name and secret pairs."""
        return self.protocols.KeySet(keys=tuple(starmap(AppKey, keys)))

    def signature(self, delivery_id: object, timestamp: object, matched_key_id: object = "scripted") -> Any:
        """Return a verified signature record with any field values, as a verifier might."""
        return self.protocols.VerifiedSignature(
            delivery_id=delivery_id, timestamp=timestamp, matched_key_id=matched_key_id
        )

    def check(
        self,
        label: str,
        vector: Vector,
        verifier: Any,
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
            self.key_set((vector.key_id, vector.secret)) if keys is None else keys,
        )
        moment = vector.now if now is None else now
        sync = outcome(lambda: helper.verify(*arguments, verifier=verifier, now=moment, **options))
        asynchronous = outcome(
            lambda: asyncio.run(helper.verify_async(*arguments, verifier=verifier, now=moment, **options))
        )
        self.lines.append(f"  {label} = {sync}")
        if asynchronous != sync:
            self.lines.append(f"  {label} async = {asynchronous}")


def imports(package: ModuleType, lines: list[str], modules: tuple[str, ...]) -> None:
    """Import modules of a package in order in a fresh process, reporting what each loads and each helper's hints."""
    if (location := package.__file__) is None:
        msg = "Generated package has no source path"
        raise RuntimeError(msg)
    source = Path(location).parent.parent
    models_file = f"{package.__name__}_models.py"
    models = next(parent for parent in (source, *source.parents) if (parent / models_file).is_file())
    completed = subprocess.run(
        [sys.executable, "-I", "-c", _IMPORT_PROBE, str(models), str(source), package.__name__, *modules],
        check=True,
        capture_output=True,
        text=True,
    )
    lines.extend(f"  {line.replace(package.__name__, '<package>')}" for line in completed.stdout.splitlines())


def webhook_adapters(package: ModuleType, lines: list[str]) -> None:
    """Verify through adapters: vectors, rejections, windows, the contract fence, call contract, and limits."""
    hooks = Adapters(package, lines)
    imports(
        package,
        lines,
        (".protocols", ".webhooks", ".webhooks.unsigned.message", ".webhooks.stripe.event", ".webhooks.mapped.event"),
    )
    _vectors(hooks)
    _rejections(hooks)
    _windows(hooks)
    _fence(hooks)
    _spoofs(hooks)
    _zones(hooks)
    _verifier_errors(hooks)
    _calls(hooks)
    _limits(hooks)
    _secrecy(hooks)


def _vectors(hooks: Adapters) -> None:
    """Accept every vector through its adapter and route mapped events by their body's type name."""
    stripe, standard, github = (hooks.verifier(kind) for kind in (StripeVerifier, StandardVerifier, GithubVerifier))
    for vector in (STRIPE_INVOICE, STRIPE_CUSTOMER, STRIPE_UPDATED, STRIPE_VOIDED):
        hooks.check(vector.source, vector, stripe)
    hooks.check(STANDARD_ADAPTED.source, STANDARD_ADAPTED, standard)
    hooks.check(GITHUB_MESSAGE.source, GITHUB_MESSAGE, github)
    hooks.check(f"{GITHUB_HELLO.source}, not JSON", GITHUB_HELLO, github)
    for vector in MAPPED:
        helper = hooks.helper(vector.helper)
        keys = hooks.protocols.KeySet(
            keys=(
                importlib.import_module(f"{hooks.package.__name__}.webhooks.keys").HmacKey(
                    id="mapped", secret=vector.secret
                ),
            )
        )
        hooks.lines.append(
            f"  {vector.source} = "
            + outcome(
                lambda helper=helper, vector=vector, keys=keys: helper.verify(
                    vector.body, list(vector.headers), keys, now=vector.now
                )
            )
        )


def _rejections(hooks: Adapters) -> None:
    """Propagate the adapter's own rejections, and try keys in key-set order during rotation."""
    stripe = hooks.verifier(StripeVerifier)
    hooks.check("one changed body byte", STRIPE_INVOICE, stripe, body=_INVOICE.replace(b"1200", b"1201"))
    later = STRIPE_INVOICE.changed("Stripe-Signature", f"t=1614265331,{STRIPE_INVOICE.headers[1][1].split(',')[1]}")
    hooks.check("changed timestamp", STRIPE_INVOICE, stripe, headers=later)
    hooks.check("no signature header", STRIPE_INVOICE, stripe, headers=STRIPE_INVOICE.changed("Stripe-Signature", None))
    hooks.check("empty key set", STRIPE_INVOICE, stripe, keys=hooks.key_set())
    retired, active = ("stripe-2025", _RETIRED_SECRET), ("stripe-2026", _STRIPE_SECRET)
    hooks.check("rotation tries the retired key first", STRIPE_UPDATED, stripe, keys=hooks.key_set(retired, active))
    hooks.check("rotation with the active key only", STRIPE_UPDATED, stripe, keys=hooks.key_set(active))
    hooks.check("retired key only", STRIPE_INVOICE, stripe, keys=hooks.key_set(retired))
    hooks.check(
        "Standard Webhooks changed delivery id",
        STANDARD_ADAPTED,
        hooks.verifier(StandardVerifier),
        headers=STANDARD_ADAPTED.changed("webhook-id", "msg_p5jXN8AQM9LWM0D4loKWxJel"),
    )


def _windows(hooks: Adapters) -> None:
    """Check the verified timestamp against the inclusive window, as builtin helpers do."""
    stripe = hooks.verifier(StripeVerifier)
    for label, now, options in (
        ("300 seconds past", _STAMP + timedelta(seconds=300), {}),
        ("past bound exceeded", _STAMP + timedelta(seconds=300) + _MICROSECOND, {}),
        ("30 seconds future", _STAMP - timedelta(seconds=30), {}),
        ("future bound exceeded", _STAMP - timedelta(seconds=30) - _MICROSECOND, {}),
        ("zero past tolerance exceeded", _STAMP + _MICROSECOND, {"past_tolerance": 0}),
        ("offset clock", _STAMP.astimezone(timezone(timedelta(hours=-5))), {}),
    ):
        arguments = {"options": hooks.protocols.WebhookOptions(**options)} if options else {}
        hooks.check(label, STRIPE_INVOICE, stripe, now=now, **arguments)
    offset = hooks.signature("msg_1", datetime(2021, 2, 26, 0, 2, 10, tzinfo=timezone(timedelta(hours=9))))
    hooks.check("verifier timestamp with an offset", replace(STANDARD_ADAPTED, body=b'{"test": 1}'), Scripted(offset))


def _fence(hooks: Adapters) -> None:
    """Refuse every result that breaks the verifier contract before decoding a body that is not JSON or claiming it."""
    vector = replace(STANDARD_ADAPTED, body=b"not JSON")
    signature = hooks.signature
    for label, answer in (
        ("control: a kept contract decodes the body", signature("msg_1", _STAMP)),
        ("mapping result", {"delivery_id": "msg_1", "timestamp": _STAMP, "matched_key_id": "scripted"}),
        ("None result", None),
        ("missing timestamp", signature("msg_1", None)),
        ("missing delivery id", signature(None, _STAMP)),
        ("empty delivery id", signature("", _STAMP)),
        ("integer delivery id", signature(7, _STAMP)),
        ("bytes delivery id", signature(b"msg_1", _STAMP)),
        ("text timestamp", signature("msg_1", "2021-02-25T15:02:10Z")),
        ("date timestamp", signature("msg_1", date(2021, 2, 25))),
        ("naive timestamp", signature("msg_1", _STAMP.replace(tzinfo=None))),
        ("empty matched key id", signature("msg_1", _STAMP, "")),
        ("integer matched key id", signature("msg_1", _STAMP, 1)),
    ):
        _fenced(hooks, label, vector, Scripted(answer))
    _fenced(hooks, "coroutine result", vector, AsyncVerifier())
    plain = replace(GITHUB_MESSAGE, body=b"not JSON")
    _fenced(hooks, "undeclared timestamp", plain, Scripted(signature(None, _STAMP)))
    _fenced(hooks, "undeclared delivery id", plain, Scripted(signature("msg_1", None)))
    _fenced(hooks, "control: no facts declared or returned", plain, Scripted(signature(None, None)))


def _fenced(hooks: Adapters, label: str, vector: Vector, verifier: Any) -> None:
    """Verify in both modes, reporting the outcome and coroutine disposal warnings."""
    helper, keys = hooks.helper(vector.helper), hooks.key_set(("scripted", b"secret"))
    results = []
    for asynchronous in (False, True):
        arguments = {"verifier": verifier, "now": vector.now}
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            if asynchronous:
                result = outcome(
                    lambda arguments=arguments: asyncio.run(
                        helper.verify_async(vector.body, list(vector.headers), keys, **arguments)
                    )
                )
            else:
                result = outcome(
                    lambda arguments=arguments: helper.verify(vector.body, list(vector.headers), keys, **arguments)
                )
            gc.collect()
        results.append(f"{result} warnings {[str(item.message) for item in caught]}")
    hooks.lines.append(f"  {label} = {results[0]}")
    if results[1] != results[0]:
        hooks.lines.append(f"  {label} async = {results[1]}")


class _Truthy(str):  # noqa: FURB189 - the verifier must accept a real str subclass
    """A string that claims to be nonempty."""

    __slots__ = ()

    def __bool__(self) -> bool:
        """Claim to be nonempty, whatever the text."""
        return True


class _Raising(str):  # noqa: FURB189 - the verifier must accept a real str subclass
    """A string whose truth test raises."""

    __slots__ = ()

    def __bool__(self) -> bool:
        """Raise instead of answering."""
        msg = "MARKER-BOOL"
        raise RuntimeError(msg)


def _spoofs(hooks: Adapters) -> None:
    """Refuse results that only claim the contract's types: subclasses, spoofed classes, and lying strings."""
    vector = replace(STANDARD_ADAPTED, body=b"not JSON")
    record_type = hooks.protocols.VerifiedSignature

    class Changing(record_type):
        """A result subclass whose matched key id changes between reads."""

        __slots__ = ("reads",)

        def __init__(self) -> None:
            """Start with valid facts."""
            object.__setattr__(self, "reads", 0)
            super().__init__(delivery_id="msg_1", timestamp=_STAMP, matched_key_id="scripted")

        def __getattribute__(self, name: str) -> Any:
            """Answer a valid id on the first read of matched_key_id and an integer afterwards."""
            if name != "matched_key_id":
                return object.__getattribute__(self, name)
            reads = object.__getattribute__(self, "reads")
            object.__setattr__(self, "reads", reads + 1)
            return "scripted" if reads == 0 else 12345

    class Spoofed:
        """An object whose __class__ claims to be a verified signature."""

        delivery_id, timestamp, matched_key_id = "msg_1", _STAMP, "scripted"

        @property
        def __class__(self) -> type:  # type: ignore[override]
            """Claim the record's class."""
            return record_type

    signature = hooks.signature
    for label, answer in (
        ("result subclass changing between reads", Changing()),
        ("result spoofing its class", Spoofed()),
        ("str subclass delivery id", signature(_Truthy("msg_1"), _STAMP)),
        ("empty str subclass claiming to be nonempty", signature(_Truthy(""), _STAMP)),
        ("matched key id whose truth test raises", signature("msg_1", _STAMP, _Raising("x"))),
    ):
        _fenced(hooks, label, vector, Scripted(answer))


class _Zone(tzinfo):
    """A time zone that records each offset read and answers from its script, raising a scripted error."""

    def __init__(self, *offsets: object) -> None:
        """Keep the offsets to give, in order, repeating the last one."""
        self.offsets = list(offsets)
        self.reads = 0

    def utcoffset(self, dt: datetime | None) -> Any:
        """Give the next offset, or raise it."""
        del dt
        offset = self.offsets[min(self.reads, len(self.offsets) - 1)]
        self.reads += 1
        if isinstance(offset, BaseException):
            raise offset
        return offset

    def dst(self, dt: datetime | None) -> timedelta | None:
        """Declare no daylight saving information."""
        del dt
        return None


class _Shifted(datetime):
    """A datetime subclass that overrides its offset."""

    def utcoffset(self) -> timedelta:
        """Claim UTC whatever the time zone."""
        return timedelta(0)


def _zones(hooks: Adapters) -> None:
    """Read a verified timestamp's offset once, return it in UTC, and refuse offsets that are naive in effect."""
    helper, keys = hooks.helper("adapted.message"), hooks.key_set(("scripted", b"secret"))
    local = (2021, 2, 26, 0, 2, 10)
    for label, zone, stamp in (
        ("+09:00 timestamp", timezone(timedelta(hours=9)), None),
        ("offset changing from +09:00 to UTC", lambda: _Zone(timedelta(hours=9), timedelta(0)), None),
        ("offset changing from +09:00 to naive", lambda: _Zone(timedelta(hours=9), None), None),
        ("naive offset", lambda: _Zone(None), None),
        ("offset raising", lambda: _Zone(RuntimeError("MARKER-ZONE")), None),
        ("offset beyond a day", lambda: _Zone(timedelta(hours=25)), None),
        ("datetime subclass overriding its offset", None, lambda: _Shifted(*local)),
    ):
        for asynchronous in (False, True):
            current = zone() if callable(zone) else zone
            moment = stamp() if stamp is not None else datetime(*local, tzinfo=current)
            arguments = {"verifier": Scripted(hooks.signature("msg_1", moment)), "now": _STAMP}
            call = (
                (lambda arguments=arguments: asyncio.run(helper.verify_async(b'{"test": 1}', [], keys, **arguments)))
                if asynchronous
                else (lambda arguments=arguments: helper.verify(b'{"test": 1}', [], keys, **arguments))
            )
            try:
                result = shown(call())
            except Exception as error:  # ruff: ignore[blind-except]
                result = f"{describe(error)} context={error.__context__!r}"
            reads = f" offset reads={current.reads}" if isinstance(current, _Zone) else ""
            mode = " async" if asynchronous else ""
            hooks.lines.append(f"  {label}{mode} = {result}{reads}")


def _verifier_errors(hooks: Adapters) -> None:
    """Propagate every error a verifier raises as it is, including native cancellation in both modes."""
    helper, keys = hooks.helper("adapted.message"), hooks.key_set(("scripted", b"secret"))
    body, headers = STANDARD_ADAPTED.body, list(STANDARD_ADAPTED.headers)
    for label, error in (
        ("verification error", hooks.errors.ProtocolDataError(reason="invalid_signature")),
        ("programmer error", RuntimeError("adapter bug")),
        ("cancellation", asyncio.CancelledError()),
    ):
        for asynchronous in (False, True):
            verifier = Scripted(error)
            try:
                if asynchronous:
                    asyncio.run(helper.verify_async(body, headers, keys, verifier=verifier, now=_STAMP))
                else:
                    helper.verify(body, headers, keys, verifier=verifier, now=_STAMP)
            except (Exception, asyncio.CancelledError) as raised:  # ruff: ignore[blind-except]
                mode = " async" if asynchronous else ""
                hooks.lines.append(
                    f"  {label}{mode} propagated={raised is error} calls={len(verifier.calls)} "
                    f"cause={raised.__cause__!r}"
                )


def _calls(hooks: Adapters) -> None:
    """Call the same synchronous verifier once per call with the delivery, keys, now, and resolved limits."""
    helper = hooks.helper("stripe.event")
    keys = hooks.key_set(("stripe-2026", _STRIPE_SECRET))
    headers = list(STRIPE_INVOICE.headers)
    options = hooks.protocols.WebhookOptions(max_signatures=2)
    for asynchronous in (False, True):
        verifier = Recording(hooks.verifier(StripeVerifier))
        call = (
            (
                lambda verifier=verifier: asyncio.run(
                    helper.verify_async(_INVOICE, headers, keys, verifier=verifier, now=_STAMP, options=options)
                )
            )
            if asynchronous
            else (
                lambda verifier=verifier: helper.verify(
                    _INVOICE, headers, keys, verifier=verifier, now=_STAMP, options=options
                )
            )
        )
        call()
        raw_body, ordered, given, now, limits = verifier.calls[0]
        mode = " async" if asynchronous else ""
        hooks.lines.append(
            f"  call{mode}: calls={len(verifier.calls)} body is given={raw_body is _INVOICE} headers={ordered!r} "
            f"keys are given={given is keys} now is given={now is _STAMP}"
        )
        hooks.lines.append(f"  limits{mode}={limits!r}")
    reads: list[str] = []
    opaque = hooks.protocols.KeySet(keys=(OpaqueKey(reads), OpaqueKey(reads)))
    scripted = Scripted(hooks.signature("msg_1", _STAMP))
    hooks.check("opaque keys", replace(STANDARD_ADAPTED, body=b'{"test": 1}'), scripted, keys=opaque)
    hooks.lines.append(
        f"  opaque key reads={reads} keys passed are given={all(call[2] is opaque for call in scripted.calls)}"
    )


def _limits(hooks: Adapters) -> None:
    """Refuse wrong arguments and oversized deliveries before calling the verifier."""
    options = hooks.protocols.WebhookOptions
    many = hooks.key_set(*((f"key-{index}", _STRIPE_SECRET) for index in range(9)))
    octets = sum(len(name) + len(value) for name, value in STRIPE_INVOICE.headers)
    for label, changes in (
        ("body over limit", {"options": options(max_body_bytes=len(_INVOICE) - 1)}),
        ("headers over limit", {"options": options(max_header_bytes=octets - 1)}),
        ("nine keys", {"keys": many}),
        ("nine keys allowed", {"keys": many, "options": options(max_keys=9)}),
        ("two signatures over the adapter's limit", {"options": options(max_signatures=1)}),
        ("text body", {"body": _INVOICE.decode()}),
        ("header mapping", {"headers": dict(STRIPE_INVOICE.headers)}),
        ("key tuple", {"keys": (AppKey("stripe-2026", _STRIPE_SECRET),)}),
        ("naive now", {"now": _STAMP.replace(tzinfo=None)}),
        ("options mapping", {"options": {"max_keys": 1}}),
        ("text body and no verifier", {"body": "{}", "verifier": object()}),
    ):
        verifier = Recording(hooks.verifier(StripeVerifier))
        vector = STRIPE_UPDATED if "signatures" in label else STRIPE_INVOICE
        verifier_given = changes.pop("verifier", verifier)
        hooks.check(label, vector, verifier_given, **changes)
        hooks.lines.append(f"    verifier calls in both modes={len(verifier.calls)}")
    helper, keys = hooks.helper("stripe.event"), hooks.key_set(("stripe-2026", _STRIPE_SECRET))
    for label, verifier in (("no verify", object()), ("verify not callable", _NotCallable())):
        record(
            hooks.lines,
            label,
            lambda verifier=verifier: helper.verify(
                _INVOICE, list(STRIPE_INVOICE.headers), keys, verifier=verifier, now=_STAMP
            ),
        )


class _NotCallable:
    """A verifier whose verify attribute is not callable."""

    verify = "verify"


def _secrecy(hooks: Adapters) -> None:
    """Keep the marker body, header, key, and verifier result out of everything a raised error reaches."""
    keys = hooks.key_set(("MARKER-KEY", b"MARKER-SECRET"))
    headers = [("Stripe-Signature", "t=1614265330,v1=MARKER")]
    texts: list[str] = [repr(keys)]
    for label, helper, body, verifier, now in (
        ("marker event", "stripe.event", b'{"type": "MARKER-BODY"}', Scripted(hooks.signature(None, _STAMP)), _STAMP),
        ("marker JSON", "stripe.event", b'{"type": MARKER-BODY', Scripted(hooks.signature(None, _STAMP)), _STAMP),
        ("marker result", "stripe.event", b"{}", Scripted(hooks.signature(b"MARKER-ID", _STAMP)), _STAMP),
        ("marker window", "stripe.event", b"{}", Scripted(hooks.signature(None, _STAMP)), _STAMP + timedelta(days=1)),
        ("marker unsigned", "unsigned.event", b'{"meta": {"type": "MARKER-TYPE"}}', None, None),
    ):
        module = hooks.helper(helper)
        try:
            if verifier is None:
                module.decode_unverified(body)
            else:
                module.verify(body, headers, keys, verifier=verifier, now=now)
        except Exception as error:  # ruff: ignore[blind-except]
            hooks.lines.append(f"  {label} ! {describe(error)} context={error.__context__!r}")
            texts.extend(reachable(error, set()))
    hooks.lines.append(f"  markers shown={[text for text in texts if 'MARKER' in text]}")


def webhook_unsigned(package: ModuleType, lines: list[str]) -> None:
    """Decode unsigned deliveries, mapped by a nested body member, without authenticating or claiming them."""
    hooks = Adapters(package, lines)
    single, mapped = hooks.helper("unsigned.message"), hooks.helper("unsigned.event")
    lines.append(f"  public names={single.__all__} has verify={hasattr(single, 'verify')}")
    invoice = b'{"meta": {"type": "invoice.paid"}, "type": "invoice", "id": "in_1", "amount": 5}'
    for label, module, body, options in (
        ("message", single, b'{"test": 1}', None),
        ("invoice by nested type", mapped, invoice, None),
        (
            "customer",
            mapped,
            b'{"meta": {"type": "customer.created"}, "type": "c", "id": "cus_1", "email": "a@b"}',
            None,
        ),
        ("no type member", mapped, b'{"id": "in_1"}', None),
        ("meta not an object", mapped, b'{"meta": 1}', None),
        ("null type", mapped, b'{"meta": {"type": null}}', None),
        ("numeric type", mapped, b'{"meta": {"type": 1}}', None),
        ("list type", mapped, b'{"meta": {"type": ["invoice.paid"]}}', None),
        ("unknown type", mapped, b'{"meta": {"type": "invoice.voided"}}', None),
        ("type in another case", mapped, b'{"meta": {"type": "Invoice.paid"}}', None),
        ("type with a space", mapped, b'{"meta": {"type": "invoice.paid "}}', None),
        ("known type, invalid event", mapped, b'{"meta": {"type": "invoice.paid"}, "id": "in_1"}', None),
        ("not JSON", mapped, b"{", None),
        ("body at limit", single, b'{"test": 1}', {"max_body_bytes": 11}),
        ("body over limit", single, b'{"test": 1}', {"max_body_bytes": 10}),
        ("text body", single, '{"test": 1}', None),
        ("bytearray body", single, bytearray(b'{"test": 1}'), None),
    ):
        arguments = {} if options is None else {"options": hooks.protocols.WebhookOptions(**options)}
        record(
            lines,
            label,
            lambda module=module, body=body, arguments=arguments: module.decode_unverified(body, **arguments),
        )
    record(lines, "options mapping", lambda: single.decode_unverified(b'{"test": 1}', options={"max_body_bytes": 1}))
    positional = failure(lambda: single.decode_unverified(b'{"test": 1}', []))
    lines.append(f"  headers argument={positional}")


def webhook_mapped_backends(package: ModuleType, lines: list[str]) -> None:
    """Decode mapped events into each backend's types, unsigned and through an adapter."""
    hooks = Adapters(package, lines)
    mapped = hooks.helper("unsigned.event")
    for body in (
        b'{"meta": {"type": "invoice.paid"}, "type": "invoice", "id": "in_1", "amount": 5}',
        b'{"meta": {"type": "customer.created"}, "type": "c", "id": "cus_1", "email": "a@b"}',
    ):
        record(lines, body.decode(), lambda body=body: mapped.decode_unverified(body))
    stripe = hooks.verifier(StripeVerifier)
    for vector in (STRIPE_INVOICE, STRIPE_CUSTOMER):
        hooks.check(vector.helper, vector, stripe)


def webhook_adapter_imports(package: ModuleType, lines: list[str]) -> None:
    """Import a package whose helpers are only an adapter and an unsigned one: no builtin signature module loads."""
    imports(
        package,
        lines,
        ("", ".protocols", ".webhooks", ".webhooks.keys", ".webhooks.unsigned.message", ".webhooks.adapted.message"),
    )
