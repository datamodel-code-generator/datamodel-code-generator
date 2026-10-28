"""Safety helpers for release benchmark documentation data."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

EntryField = tuple[str, re.Pattern[str]]

MAIN_VERSION = "main"
MAIN_SNAPSHOT_METADATA_KEYS = (
    "main_snapshot_generated_at",
    "main_snapshot_workflow_run_id",
    "main_snapshot_collector_sha",
)
MAIN_SNAPSHOT_FALLBACK_KEYS = {
    "main_snapshot_generated_at": "generated_at",
    "main_snapshot_workflow_run_id": "workflow_run_id",
    "main_snapshot_collector_sha": "collector_sha",
}
SAFE_LABEL_PATTERN = re.compile(r"^(?:main|[A-Za-z0-9][A-Za-z0-9._+-]*)$")
SAFE_FIELD_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
SAFE_METADATA_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._:/+-]*$")
SAFE_TIMESTAMP_PATTERN = re.compile(r"^[0-9T:Z+ .-]*$")
METADATA_VERSION_LIST_KEYS = frozenset({"selected_versions"})
ENTRY_FIELDS: tuple[EntryField, ...] = (
    ("version", SAFE_LABEL_PATTERN),
    ("input_type", SAFE_FIELD_PATTERN),
    ("case", SAFE_FIELD_PATTERN),
    ("formatter", SAFE_FIELD_PATTERN),
)
OPTIONAL_ENTRY_FIELDS: tuple[EntryField, ...] = (
    ("python_version", SAFE_METADATA_PATTERN),
    ("os", SAFE_METADATA_PATTERN),
    ("status", SAFE_FIELD_PATTERN),
)
INCOMING_METADATA_KEYS = (
    "generated_at",
    "workflow",
    "workflow_run_id",
    "collector_sha",
    "os",
    "python_version",
    "runs_per_case",
    "warmups_per_case",
    "selection_strategy",
    "selection_window_days",
    "selected_versions",
    "download_source",
    "download_window_days",
    "download_versions",
    "download_window_total",
    "pypistats_source",
    "pypistats_last_day",
    "pypistats_last_week",
    "pypistats_last_month",
    "pypistats_timeseries_days",
    "pypistats_timeseries_downloads",
    *MAIN_SNAPSHOT_METADATA_KEYS,
)


def string(value: object) -> str:
    """Return a benchmark string value or an empty string."""
    return value if isinstance(value, str) else ""


def version_sort_key(version: str) -> tuple[int, int, int, int, int, str]:
    """Sort release-like versions before the synthetic main benchmark."""
    normalized = version.strip().removeprefix("v")
    if normalized == "main":
        return 1, 0, 0, 0, 0, normalized

    numbers: list[int] = []
    suffix_parts: list[str] = []
    for token in re.split(r"[.\-+_]", normalized):
        if token.isdecimal():
            numbers.append(int(token))
        elif token:
            suffix_parts.append(token)
    numbers.extend([0] * 4)
    return 0, numbers[0], numbers[1], numbers[2], numbers[3], ".".join(suffix_parts)


def unsafe_value_message(path: Path, field: str, value: str, *, index: int | None = None) -> str:
    """Build a stable validation error for unsafe benchmark metadata."""
    location = f"Benchmark entry #{index}" if isinstance(index, int) else "Benchmark metadata"
    return f"{location} in {path} has unsafe {field}: {value!r}"


def safe_required_field(
    entry: Mapping[str, object],
    field: str,
    pattern: re.Pattern[str],
    *,
    path: Path,
    index: int,
) -> str:
    """Return a required string field after validating it against a safe pattern."""
    if not (value := string(entry.get(field)).strip()):
        msg = f"Benchmark entry #{index} in {path} is missing {field}"
        raise ValueError(msg)
    if pattern.fullmatch(value):
        return value
    raise ValueError(unsafe_value_message(path, field, value, index=index))


def safe_optional_field(
    entry: Mapping[str, object],
    field: str,
    pattern: re.Pattern[str],
    *,
    path: Path,
    index: int,
) -> str:
    """Return an optional string field after validating it against a safe pattern."""
    if not (value := string(entry.get(field)).strip()):
        return ""
    if pattern.fullmatch(value):
        return value
    raise ValueError(unsafe_value_message(path, field, value, index=index))


def validated_benchmark_entry(raw: Mapping[str, object], *, path: Path, index: int) -> dict[str, object]:
    """Validate fields that are later used in Markdown, HTML attributes, or paths."""
    entry = dict(raw)
    for field, pattern in ENTRY_FIELDS:
        entry[field] = safe_required_field(entry, field, pattern, path=path, index=index)
    for field, pattern in OPTIONAL_ENTRY_FIELDS:
        if value := safe_optional_field(entry, field, pattern, path=path, index=index):
            entry[field] = value
    return entry


def safe_release_version(value: object, *, path: Path, field: str, index: int | None = None) -> str:
    """Return a release version that is safe for docs labels and artifact names."""
    if not isinstance(value, str) or not (version := value.strip()):
        return ""
    if SAFE_LABEL_PATTERN.fullmatch(version):
        return version
    raise ValueError(unsafe_value_message(path, field, version, index=index))


def safe_timestamp(value: object, *, path: Path, field: str, index: int | None = None) -> str:
    """Return a timestamp that cannot introduce Markdown or HTML tokens."""
    if not isinstance(value, str) or not (uploaded_at := value.strip()):
        return ""
    if SAFE_TIMESTAMP_PATTERN.fullmatch(uploaded_at):
        return uploaded_at
    raise ValueError(unsafe_value_message(path, field, uploaded_at, index=index))


def safe_release_dates(raw_dates: Mapping[object, object], *, path: Path) -> dict[str, str]:
    """Validate the release date mapping before it is rendered into docs."""
    release_dates: dict[str, str] = {}
    for version, uploaded_at in raw_dates.items():
        safe_version = safe_release_version(version, path=path, field="release_dates version")
        safe_uploaded_at = safe_timestamp(uploaded_at, path=path, field=f"release_dates.{safe_version}")
        if safe_version:
            release_dates[safe_version] = safe_uploaded_at
    return release_dates


def safe_metadata_value(metadata: Mapping[str, object], key: str, *, path: Path) -> str:
    """Return an allowlisted metadata scalar as a safe string."""
    value = metadata.get(key)
    if key in METADATA_VERSION_LIST_KEYS and isinstance(value, str):
        versions = [
            version
            for raw_version in value.split(",")
            if (version := safe_release_version(raw_version, path=path, field=key))
        ]
        return ",".join(versions)
    match value:
        case int() | float():
            text = str(value)
        case str():
            text = value.strip()
        case _:
            return ""
    if not text:
        return ""
    if SAFE_METADATA_PATTERN.fullmatch(text):
        return text
    raise ValueError(unsafe_value_message(path, key, text))


def safe_incoming_metadata(metadata: Mapping[str, object], *, path: Path) -> dict[str, str]:
    """Filter incoming workflow metadata to the fields rendered by docs."""
    return {
        key: safe_value
        for key in INCOMING_METADATA_KEYS
        if (safe_value := safe_metadata_value(metadata, key, path=path))
    }
