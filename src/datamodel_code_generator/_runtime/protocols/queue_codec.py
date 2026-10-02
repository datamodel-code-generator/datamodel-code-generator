"""Closed version-one JSON inventories for persistent queue records; payload bytes remain opaque."""

from __future__ import annotations

import base64
import json
from datetime import datetime
from math import isfinite
from typing import Final, cast

from ..client.responses import HeadersView, ResponseInfo
from .queue_state import entry_value, instant
from .queues import BlobRef, OutcomeCategory, QueueEntry, QueueOutcome, QueueState, ResolvedQueueOptions

_HEADER_PAIR_SIZE: Final = 2
_MIN_STATUS: Final = 100
_MAX_STATUS: Final = 599
_ENTRY_FIELDS: Final = frozenset({
    "entry_id",
    "version",
    "operation_alias",
    "helper_fingerprint",
    "security_fingerprint",
    "payload",
    "blob",
    "blob_owned",
    "idempotency_key",
    "created_at",
    "expires_at",
    "not_before",
    "saved_wait_seconds",
    "state",
    "delivery_count",
    "send_intent",
    "cancel_requested",
    "lease_id",
    "lease_until",
    "policy",
    "result",
})
_POLICY_FIELDS: Final = frozenset({
    "max_entries",
    "parallelism",
    "max_entry_body_bytes",
    "max_deliveries",
    "entry_ttl",
    "retry_initial_delay",
    "retry_max_delay",
    "lease_min",
    "lease_grace",
    "max_delivery_timeout",
})
_RESPONSE_FIELDS: Final = frozenset({
    "status_code",
    "headers",
    "call_id",
    "elapsed",
    "content_type",
    "request_id",
    "resource_attempt_count",
    "redirect_count",
    "auth_exchange_count",
    "network_send_count",
    "network_send_budget_used",
    "auth_exchange_budget_used",
    "auth_refresh_ids",
    "auth_refresh_pending",
    "wire_send_count",
})


def _object(value: object, fields: frozenset[str]) -> dict[str, object]:
    if not isinstance(value, dict):
        msg = "invalid queue record field inventory"
        raise TypeError(msg)
    record = cast("dict[object, object]", value)
    if frozenset(record) != fields:
        msg = "invalid queue record field inventory"
        raise ValueError(msg)
    return cast("dict[str, object]", record)


def _string(value: object) -> str:
    if not isinstance(value, str):
        msg = "queue record string required"
        raise TypeError(msg)
    return value


def _optional_string(value: object) -> str | None:
    return None if value is None else _string(value)


def _count(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        msg = "queue record nonnegative integer required"
        raise ValueError(msg)
    return value


def _number(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not isfinite(value) or value < 0:
        msg = "queue record finite nonnegative number required"
        raise ValueError(msg)
    return float(value)


def _boolean(value: object) -> bool:
    if not isinstance(value, bool):
        msg = "queue record boolean required"
        raise TypeError(msg)
    return value


def _bytes(value: object) -> bytes:
    text = _string(value)
    decoded = base64.b64decode(text, validate=True)
    if base64.b64encode(decoded).decode("ascii") != text:
        msg = "noncanonical queue record bytes"
        raise ValueError(msg)
    return decoded


def _date(value: object) -> datetime:
    return instant(datetime.fromisoformat(_string(value)), "record_time")


def _optional_date(value: object) -> datetime | None:
    return None if value is None else _date(value)


def _datestring(value: datetime | None) -> str | None:
    return None if value is None else instant(value, "record_time").isoformat()


def _policy(value: object) -> ResolvedQueueOptions:
    record = _object(value, _POLICY_FIELDS)
    return ResolvedQueueOptions(
        max_entries=_count(record["max_entries"]),
        parallelism=_count(record["parallelism"]),
        max_entry_body_bytes=_count(record["max_entry_body_bytes"]),
        max_deliveries=_count(record["max_deliveries"]),
        entry_ttl=_number(record["entry_ttl"]),
        retry_initial_delay=_number(record["retry_initial_delay"]),
        retry_max_delay=_number(record["retry_max_delay"]),
        lease_min=_number(record["lease_min"]),
        lease_grace=_number(record["lease_grace"]),
        max_delivery_timeout=_number(record["max_delivery_timeout"]),
    )


def _policy_record(value: ResolvedQueueOptions) -> dict[str, object]:
    return {
        "max_entries": value.max_entries,
        "parallelism": value.parallelism,
        "max_entry_body_bytes": value.max_entry_body_bytes,
        "max_deliveries": value.max_deliveries,
        "entry_ttl": value.entry_ttl,
        "retry_initial_delay": value.retry_initial_delay,
        "retry_max_delay": value.retry_max_delay,
        "lease_min": value.lease_min,
        "lease_grace": value.lease_grace,
        "max_delivery_timeout": value.max_delivery_timeout,
    }


def _headers(value: object) -> HeadersView:
    if not isinstance(value, list):
        msg = "queue record ordered headers required"
        raise TypeError(msg)
    pairs: list[tuple[str, str]] = []
    for pair in cast("list[object]", value):
        if not isinstance(pair, list):
            msg = "invalid queue record header pair"
            raise TypeError(msg)
        items = cast("list[object]", pair)
        if len(items) != _HEADER_PAIR_SIZE:
            msg = "invalid queue record header pair"
            raise ValueError(msg)
        pairs.append((_string(items[0]), _string(items[1])))
    return HeadersView(pairs)


def _strings(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        msg = "queue record string array required"
        raise TypeError(msg)
    return tuple(_string(item) for item in cast("list[object]", value))


def _response(value: object) -> ResponseInfo | None:
    if value is None:
        return None
    record = _object(value, _RESPONSE_FIELDS)
    status = _count(record["status_code"])
    if not _MIN_STATUS <= status <= _MAX_STATUS:
        msg = "invalid queue record response status"
        raise ValueError(msg)
    return ResponseInfo(
        status_code=status,
        headers=_headers(record["headers"]),
        call_id=_string(record["call_id"]),
        elapsed=_number(record["elapsed"]),
        content_type=_optional_string(record["content_type"]),
        request_id=_optional_string(record["request_id"]),
        resource_attempt_count=_count(record["resource_attempt_count"]),
        redirect_count=_count(record["redirect_count"]),
        auth_exchange_count=_count(record["auth_exchange_count"]),
        network_send_count=_count(record["network_send_count"]),
        network_send_budget_used=_count(record["network_send_budget_used"]),
        auth_exchange_budget_used=_count(record["auth_exchange_budget_used"]),
        auth_refresh_ids=_strings(record["auth_refresh_ids"]),
        auth_refresh_pending=_count(record["auth_refresh_pending"]),
        wire_send_count=None if record["wire_send_count"] is None else _count(record["wire_send_count"]),
    )


def _response_record(value: ResponseInfo | None) -> dict[str, object] | None:
    if value is None:
        return None
    record: dict[str, object] = {
        "status_code": value.status_code,
        "headers": [list(pair) for pair in value.headers.items()],
        "call_id": value.call_id,
        "elapsed": value.elapsed,
        "content_type": value.content_type,
        "request_id": value.request_id,
        "resource_attempt_count": value.resource_attempt_count,
        "redirect_count": value.redirect_count,
        "auth_exchange_count": value.auth_exchange_count,
        "network_send_count": value.network_send_count,
        "network_send_budget_used": value.network_send_budget_used,
        "auth_exchange_budget_used": value.auth_exchange_budget_used,
        "auth_refresh_ids": list(value.auth_refresh_ids),
        "auth_refresh_pending": value.auth_refresh_pending,
        "wire_send_count": value.wire_send_count,
    }
    _response(record)
    return record


def _outcome(value: object) -> QueueOutcome | None:
    if value is None:
        return None
    record = _object(value, frozenset({"category", "response", "retry_at", "error_code"}))
    return QueueOutcome(
        category=cast("OutcomeCategory", _string(record["category"])),
        response=_response(record["response"]),
        retry_at=_optional_date(record["retry_at"]),
        error_code=_optional_string(record["error_code"]),
    )


def _outcome_record(value: QueueOutcome | None) -> dict[str, object] | None:
    return (
        None
        if value is None
        else {
            "category": value.category,
            "response": _response_record(value.response),
            "retry_at": _datestring(value.retry_at),
            "error_code": value.error_code,
        }
    )


def _blob(value: object) -> BlobRef | None:
    if value is None:
        return None
    record = _object(value, frozenset({"size", "sha256", "key"}))
    return BlobRef(size=_count(record["size"]), sha256=_bytes(record["sha256"]), key=_string(record["key"]))


def _pairs(items: list[tuple[str, object]]) -> dict[str, object]:
    record = dict(items)
    if len(record) != len(items):
        msg = "duplicate queue record fields"
        raise ValueError(msg)
    return record


def decode(text: str) -> QueueEntry:
    """Decode exactly the v1 envelope and complete public field inventory, refusing malformed records."""
    envelope = _object(json.loads(text, object_pairs_hook=_pairs), frozenset({"schema_version", "entry"}))
    if type(envelope["schema_version"]) is not int or envelope["schema_version"] != 1:
        msg = "unsupported queue record version"
        raise ValueError(msg)
    record = _object(envelope["entry"], _ENTRY_FIELDS)
    return QueueEntry(
        entry_id=_string(record["entry_id"]),
        version=_string(record["version"]),
        operation_alias=_string(record["operation_alias"]),
        helper_fingerprint=_string(record["helper_fingerprint"]),
        security_fingerprint=_string(record["security_fingerprint"]),
        payload=_bytes(record["payload"]),
        blob=_blob(record["blob"]),
        blob_owned=_boolean(record["blob_owned"]),
        idempotency_key=_optional_string(record["idempotency_key"]),
        created_at=_date(record["created_at"]),
        expires_at=_date(record["expires_at"]),
        not_before=_date(record["not_before"]),
        saved_wait_seconds=_number(record["saved_wait_seconds"]),
        state=cast("QueueState", _string(record["state"])),
        delivery_count=_count(record["delivery_count"]),
        send_intent=_boolean(record["send_intent"]),
        cancel_requested=_boolean(record["cancel_requested"]),
        lease_id=_optional_string(record["lease_id"]),
        lease_until=_optional_date(record["lease_until"]),
        policy=_policy(record["policy"]),
        result=_outcome(record["result"]),
    )


def encode(entry: QueueEntry) -> str:
    """Encode the explicit record fields as canonical JSON, without interpreting or changing opaque payload bytes."""
    entry = entry_value(entry)
    blob = entry.blob
    record = {
        "entry_id": entry.entry_id,
        "version": entry.version,
        "operation_alias": entry.operation_alias,
        "helper_fingerprint": entry.helper_fingerprint,
        "security_fingerprint": entry.security_fingerprint,
        "payload": base64.b64encode(entry.payload).decode("ascii"),
        "blob": None
        if blob is None
        else {
            "size": blob.size,
            "sha256": base64.b64encode(blob.sha256).decode("ascii"),
            "key": blob.key,
        },
        "blob_owned": entry.blob_owned,
        "idempotency_key": entry.idempotency_key,
        "created_at": _datestring(entry.created_at),
        "expires_at": _datestring(entry.expires_at),
        "not_before": _datestring(entry.not_before),
        "saved_wait_seconds": entry.saved_wait_seconds,
        "state": entry.state,
        "delivery_count": entry.delivery_count,
        "send_intent": entry.send_intent,
        "cancel_requested": entry.cancel_requested,
        "lease_id": entry.lease_id,
        "lease_until": _datestring(entry.lease_until),
        "policy": _policy_record(entry.policy),
        "result": _outcome_record(entry.result),
    }
    return json.dumps({"schema_version": 1, "entry": record}, sort_keys=True, separators=(",", ":"), allow_nan=False)
