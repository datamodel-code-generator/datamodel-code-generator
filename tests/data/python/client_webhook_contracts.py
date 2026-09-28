"""Exercise generated webhook records, option boundaries, and atomic bounded replay stores."""

from __future__ import annotations

import asyncio
import importlib
import inspect
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import fields
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Barrier
from time import sleep
from typing import TYPE_CHECKING, Final, get_args, get_origin, get_type_hints

from tests.data.python.client_runtime import arecord, record, run

if TYPE_CHECKING:
    from types import ModuleType

_NOW: Final = datetime(2026, 9, 28, tzinfo=timezone.utc)
_COUNTS: Final = ("max_body_bytes", "max_header_bytes", "max_keys", "max_signatures")
_SECONDS: Final = ("past_tolerance", "future_tolerance", "replay_ttl")
_IMPORT_PROBE: Final = """
import importlib
import sys
import threading
sys.path.insert(0, sys.argv[1])
before = threading.active_count()
module = importlib.import_module(sys.argv[2] + '.protocols')
optional = ('httpx2', 'httpcore2', 'cryptography', 'asyncio', 'pydantic', 'msgspec', 'anyio')
print('optional imports=' + repr([name for name in optional if name in sys.modules]))
print('import threads unchanged=' + repr(threading.active_count() == before))
print('replay loaded with types=' + repr(sys.argv[2] + '._runtime.protocols.replay' in sys.modules))
module.MemoryReplayStore()
module.AsyncMemoryReplayStore()
print('replay loaded with stores=' + repr(sys.argv[2] + '._runtime.protocols.replay' in sys.modules))
print('construction threads unchanged=' + repr(threading.active_count() == before))
"""


def webhook_contracts(package: ModuleType, lines: list[str]) -> None:
    """Report the public generated shapes and independent sync/async replay-store behavior."""
    protocols = importlib.import_module(f"{package.__name__}.protocols")
    errors = importlib.import_module(f"{package.__name__}.errors")
    options = importlib.import_module(f"{package.__name__}.options")
    _imports(package, lines)
    _records(protocols, options, lines)
    _options(protocols, lines)
    _stores(protocols, errors, lines)
    run(lambda: _async_stores(protocols, errors, lines))


def _imports(package: ModuleType, lines: list[str]) -> None:
    """Import shared contracts in a fresh process before any test fixture imports HTTP libraries."""
    if (location := package.__file__) is None:
        msg = "Generated package has no source path"
        raise RuntimeError(msg)
    root = Path(location).parent.parent
    completed = subprocess.run(
        [sys.executable, "-I", "-c", _IMPORT_PROBE, str(root), package.__name__],
        check=True,
        capture_output=True,
        text=True,
    )
    lines.extend(f"  {line}" for line in completed.stdout.splitlines())


def _records(protocols: ModuleType, options: ModuleType, lines: list[str]) -> None:
    """Preserve opaque identities, expose the declared fields, and reject mutation or positional records."""
    first, second = object(), object()
    original = (first, second)
    keys = protocols.KeySet(keys=original)
    lines.append(f"  key identities={keys.keys is original}/{keys.keys[0] is first}/{keys.keys[1] is second}")
    record(lines, "opaque key representation", lambda: keys)
    record(lines, "empty keys", lambda: protocols.KeySet(keys=()))
    record(lines, "no constructor key limit", lambda: len(protocols.KeySet(keys=(first,) * 9).keys))
    for label, value in (
        ("list", [first]),
        ("iterator", iter(original)),
        ("mapping", {"key": first}),
        ("string", "secret material"),
        ("None", None),
    ):
        record(lines, f"keys {label}", lambda value=value: protocols.KeySet(keys=value))
    record(lines, "keys frozen", lambda: setattr(keys, "keys", ()))
    record(lines, "keys keyword-only", lambda: protocols.KeySet(original))
    operation = protocols.OperationRef(pointer="/paths/~1hooks/post")
    record(lines, "operation reference", lambda: operation)
    record(
        lines, "document reference", lambda: protocols.OperationRef(pointer="/paths/~1hooks/post", document="api.yaml")
    )
    record(lines, "reference frozen", lambda: setattr(operation, "document", "elsewhere.yaml"))
    record(lines, "reference keyword-only", lambda: protocols.OperationRef("/paths/~1hooks/post"))
    signature = protocols.VerifiedSignature(delivery_id="delivery", timestamp=_NOW, matched_key_id="active")
    record(lines, "signature facts", lambda: signature)
    record(
        lines,
        "signature no optional facts",
        lambda: protocols.VerifiedSignature(delivery_id=None, timestamp=None, matched_key_id="active"),
    )
    record(lines, "signature frozen", lambda: setattr(signature, "matched_key_id", "other"))
    record(lines, "signature fields required", lambda: protocols.VerifiedSignature(matched_key_id="active"))
    payload = {"secret": "not in the representation"}
    webhook = protocols.VerifiedWebhook(
        data=payload, delivery_id="delivery", timestamp=_NOW, matched_key_id="active", duplicate=False
    )
    record(lines, "verified event representation", lambda: webhook)
    lines.append(f"  event data identity={webhook.data is payload}")
    record(lines, "event frozen", lambda: setattr(webhook, "duplicate", True))
    unresolved = protocols.WebhookOptions()
    record(lines, "omitted options", lambda: unresolved)
    lines.append(
        f"  options share UNSET={all(getattr(unresolved, name) is options.UNSET for name in (*_COUNTS, *_SECONDS))}"
    )
    resolved = protocols.ResolvedWebhookOptions(
        max_body_bytes=8388608,
        max_header_bytes=16384,
        max_keys=8,
        max_signatures=8,
        past_tolerance=300.0,
        future_tolerance=30.0,
        replay_ttl=300.0,
    )
    record(lines, "resolved options", lambda: resolved)
    record(lines, "resolved options frozen", lambda: setattr(resolved, "max_keys", 1))
    record(lines, "options frozen", lambda: setattr(unresolved, "max_keys", 1))
    for name in (
        "OperationRef",
        "KeySet",
        "VerifiedSignature",
        "VerifiedWebhook",
        "WebhookOptions",
        "ResolvedWebhookOptions",
    ):
        record_type = getattr(protocols, name)
        lines.append(f"  {name} fields={tuple(item.name for item in fields(record_type))}")
        lines.append(
            f"  {name} keyword-only={all(item.kind is inspect.Parameter.KEYWORD_ONLY for item in inspect.signature(record_type).parameters.values())}"
        )
        lines.append(f"  {name} hints={tuple(get_type_hints(record_type))}")
    key_parameter = protocols.KeySet.__parameters__[0]
    event_parameter = protocols.VerifiedWebhook.__parameters__[0]
    lines.append(f"  key variance={key_parameter.__covariant__}/{key_parameter.__contravariant__}")
    lines.append(f"  event covariance={event_parameter.__covariant__}")
    key_hint = get_type_hints(protocols.KeySet)["keys"]
    lines.append(f"  key tuple hint={get_origin(key_hint) is tuple}/{get_args(key_hint) == (key_parameter, Ellipsis)}")
    verifier_hints = get_type_hints(protocols.Verifier.verify)
    lines.append(f"  verifier key identity={protocols.Verifier.__parameters__[0] is key_parameter}")
    lines.append(
        f"  verifier hints={tuple(verifier_hints)} result={verifier_hints['return'] is protocols.VerifiedSignature}"
    )
    lines.append(f"  verifier synchronous={not inspect.iscoroutinefunction(protocols.Verifier.verify)}")
    for name in ("ReplayStore", "AsyncReplayStore", "MemoryReplayStore", "AsyncMemoryReplayStore"):
        store_type = getattr(protocols, name)
        lines.append(
            f"  {name} claim hints={tuple(get_type_hints(store_type.claim))} async={inspect.iscoroutinefunction(store_type.claim)}"
        )
    lines.append(
        f"  replay capacity defaults={inspect.signature(protocols.MemoryReplayStore).parameters['max_entries'].default}/{inspect.signature(protocols.AsyncMemoryReplayStore).parameters['max_entries'].default}"
    )
    record(lines, "unknown protocol attribute", lambda: getattr(protocols, "MissingWebhookType"))


def _options(protocols: ModuleType, lines: list[str]) -> None:
    """Reject wrong types, bool, nonfinite numbers, and every forbidden zero boundary."""
    for name in _COUNTS:
        for label, value in (
            ("bool", True),
            ("zero", 0),
            ("negative", -1),
            ("float", 1.0),
            ("None", None),
            ("string", "1"),
        ):
            record(
                lines,
                f"options {name} {label}",
                lambda name=name, value=value: protocols.WebhookOptions(**{name: value}),
            )
    for name in _SECONDS:
        for label, value in (
            ("bool", False),
            ("negative", -1),
            ("NaN", float("nan")),
            ("infinity", float("inf")),
            ("negative infinity", -float("inf")),
            ("None", None),
            ("string", "1"),
            ("unrepresentable", 10**1000),
        ):
            record(
                lines,
                f"options {name} {label}",
                lambda name=name, value=value: protocols.WebhookOptions(**{name: value}),
            )
    record(lines, "zero replay TTL", lambda: protocols.WebhookOptions(replay_ttl=0))
    record(
        lines,
        "minimum limits",
        lambda: protocols.WebhookOptions(
            max_body_bytes=1,
            max_header_bytes=1,
            max_keys=1,
            max_signatures=1,
            past_tolerance=0,
            future_tolerance=0.0,
            replay_ttl=0.01,
        ),
    )
    record(
        lines, "integer duration", lambda: protocols.WebhookOptions(past_tolerance=5, future_tolerance=7, replay_ttl=9)
    )
    record(lines, "unknown option", lambda: protocols.WebhookOptions(namespace="not an option"))


def _stores(protocols: ModuleType, errors: ModuleType, lines: list[str]) -> None:
    """Observe isolation, full capacity, expiry without extension, and atomic claims across real threads."""
    for name in ("MemoryReplayStore", "AsyncMemoryReplayStore"):
        store_type = getattr(protocols, name)
        for label, value in (("bool", True), ("zero", 0), ("negative", -1), ("float", 1.0), ("None", None)):
            record(
                lines,
                f"{name} capacity {label}",
                lambda store_type=store_type, value=value: store_type(max_entries=value),
            )
    future = datetime.now(timezone.utc) + timedelta(hours=1)
    store = protocols.MemoryReplayStore(max_entries=2)
    record(lines, "first claim", lambda: store.claim("first", "delivery", future))
    record(lines, "duplicate claim", lambda: store.claim("first", "delivery", future))
    record(lines, "independent namespace", lambda: store.claim("second", "delivery", future))
    try:
        store.claim("first", "other", future)
    except errors.ReplayStoreFullError as error:
        lines.append(f"  full store={error.reason_code} action={error.action} capacity={error.max_entries}")
    record(lines, "full store preserves first", lambda: store.claim("first", "delivery", future))
    record(lines, "full store preserves second", lambda: store.claim("second", "delivery", future))
    record(
        lines, "expired claim needs no capacity", lambda: store.claim("first", "expired", _NOW - timedelta(days=3650))
    )
    record(
        lines,
        "past expiry cannot replace live claim",
        lambda: store.claim("first", "delivery", _NOW - timedelta(days=3650)),
    )
    independent = protocols.MemoryReplayStore()
    record(lines, "independent store", lambda: independent.claim("first", "delivery", future))
    record(lines, "empty namespace and delivery", lambda: independent.claim("", "", future))
    for label, namespace, delivery, expiry in (
        ("namespace type", None, "delivery", future),
        ("delivery type", "namespace", 1, future),
        ("expiry type", "namespace", "delivery", "tomorrow"),
        ("naive expiry", "namespace", "delivery", datetime(2026, 9, 28)),
    ):
        record(
            lines,
            f"claim {label}",
            lambda namespace=namespace, delivery=delivery, expiry=expiry: independent.claim(
                namespace, delivery, expiry
            ),
        )
    east = timezone(timedelta(hours=9))
    record(lines, "aware offset claim", lambda: independent.claim("offset", "delivery", future.astimezone(east)))
    record(lines, "UTC duplicate", lambda: independent.claim("offset", "delivery", future))
    record(
        lines, "earliest offset expiry", lambda: independent.claim("limits", "past", datetime.min.replace(tzinfo=east))
    )
    record(
        lines,
        "latest offset expiry",
        lambda: independent.claim("limits", "future", datetime.max.replace(tzinfo=timezone(-timedelta(hours=9)))),
    )
    for _ in range(5):
        observations: list[str] = []
        expiring = protocols.MemoryReplayStore(max_entries=2)
        record(observations, "retain long claim", lambda: expiring.claim("expiry", "long", future))
        expires = datetime.now(timezone.utc) + timedelta(seconds=2)
        record(observations, "retain short claim", lambda: expiring.claim("expiry", "short", expires))
        record(observations, "duplicate never extends expiry", lambda: expiring.claim("expiry", "short", future))
        if datetime.now(timezone.utc) < expires:
            lines.extend(observations)
            break
    else:
        msg = "Replay expiry assertions missed all five real-clock scheduling windows"
        raise RuntimeError(msg)
    sleep(2.1)
    record(lines, "expired entry frees capacity", lambda: expiring.claim("expiry", "short", future))
    record(lines, "unexpired entry remains", lambda: expiring.claim("expiry", "long", future))
    racing = protocols.MemoryReplayStore(max_entries=1)
    barrier = Barrier(8)

    def claim(index: int) -> bool:
        del index
        barrier.wait(timeout=30)
        return racing.claim("race", "same", future)

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(claim, range(8)))
    lines.append(f"  atomic threads claimed={sum(results)} duplicate={results.count(False)}")


async def _async_stores(protocols: ModuleType, errors: ModuleType, lines: list[str]) -> None:
    """Use the asynchronous contract directly and through concurrent task and thread callers."""
    future = datetime.now(timezone.utc) + timedelta(hours=1)
    store = protocols.AsyncMemoryReplayStore(max_entries=1)
    await arecord(lines, "async first claim", lambda: store.claim("async", "delivery", future))
    await arecord(lines, "async duplicate", lambda: store.claim("async", "delivery", future))
    try:
        await store.claim("async", "other", future)
    except errors.ReplayStoreFullError as error:
        lines.append(f"  async full store={error.reason_code} action={error.action} capacity={error.max_entries}")
    racing = protocols.AsyncMemoryReplayStore()
    results = await asyncio.gather(*(racing.claim("tasks", "same", future) for _ in range(32)))
    lines.append(f"  atomic tasks claimed={sum(results)} duplicate={results.count(False)}")
    barrier = Barrier(8)

    def claim(index: int) -> bool:
        del index
        barrier.wait(timeout=30)
        return asyncio.run(racing.claim("threads", "same", future))

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(claim, range(8)))
    lines.append(f"  async atomic threads claimed={sum(results)} duplicate={results.count(False)}")
