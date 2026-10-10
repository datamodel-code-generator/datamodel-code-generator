"""Exercise generated webhook records, option boundaries, and authenticated facts and bounded verification options."""

from __future__ import annotations

import importlib
import inspect
import subprocess
import sys
from dataclasses import fields
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Final, get_args, get_origin, get_type_hints

from tests.api_generation.support.client_runtime import record

if TYPE_CHECKING:
    from types import ModuleType

_NOW: Final = datetime(2026, 9, 28, tzinfo=timezone.utc)
_COUNTS: Final = ("max_body_bytes", "max_header_bytes", "max_keys", "max_signatures")
_SECONDS: Final = ("past_tolerance", "future_tolerance")
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
"""


def webhook_contracts(package: ModuleType, lines: list[str]) -> None:
    """Report the public generated shapes and independent sync/async replay-store behavior."""
    protocols = importlib.import_module(f"{package.__name__}.protocols")
    options = importlib.import_module(f"{package.__name__}.options")
    _imports(package, lines)
    _records(protocols, options, lines)
    _options(protocols, lines)


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


def _keyword_only(record_type: type) -> bool:
    """Whether a record type takes only keyword arguments."""
    return all(
        item.kind is inspect.Parameter.KEYWORD_ONLY for item in inspect.signature(record_type).parameters.values()
    )


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
    webhook = protocols.VerifiedWebhook(data=payload, delivery_id="delivery", timestamp=_NOW, matched_key_id="active")
    record(lines, "verified event representation", lambda: webhook)
    lines.append(f"  event data identity={webhook.data is payload}")
    record(lines, "event frozen", lambda: setattr(webhook, "matched_key_id", "changed"))
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
        lines.extend((
            f"  {name} fields={tuple(item.name for item in fields(record_type))}",
            f"  {name} keyword-only={_keyword_only(record_type)}",
            f"  {name} hints={tuple(get_type_hints(record_type))}",
        ))
    key_parameter = protocols.KeySet.__parameters__[0]
    event_parameter = protocols.VerifiedWebhook.__parameters__[0]
    lines.extend((
        f"  key variance={key_parameter.__covariant__}/{key_parameter.__contravariant__}",
        f"  event covariance={event_parameter.__covariant__}",
    ))
    key_hint = get_type_hints(protocols.KeySet)["keys"]
    lines.append(f"  key tuple hint={get_origin(key_hint) is tuple}/{get_args(key_hint) == (key_parameter, Ellipsis)}")
    verifier_hints = get_type_hints(protocols.Verifier.verify)
    lines.extend((
        f"  verifier key identity={protocols.Verifier.__parameters__[0] is key_parameter}",
        f"  verifier hints={tuple(verifier_hints)} result={verifier_hints['return'] is protocols.VerifiedSignature}",
        f"  verifier synchronous={not inspect.iscoroutinefunction(protocols.Verifier.verify)}",
    ))
    record(lines, "unknown protocol attribute", lambda: protocols.MissingWebhookType)


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
        ),
    )
    record(lines, "integer duration", lambda: protocols.WebhookOptions(past_tolerance=5, future_tolerance=7))
    record(lines, "unknown option", lambda: protocols.WebhookOptions(namespace="not an option"))
