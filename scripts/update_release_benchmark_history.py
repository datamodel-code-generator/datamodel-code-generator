"""Merge a release benchmark run into the committed benchmark history.

Usage:
    python scripts/update_release_benchmark_history.py \
      --incoming-data .benchmarks/release-benchmarks.json
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

try:
    from scripts.release_benchmark_safety import (
        MAIN_SNAPSHOT_FALLBACK_KEYS,
        MAIN_SNAPSHOT_METADATA_KEYS,
        MAIN_VERSION,
        safe_incoming_metadata,
        safe_release_dates,
        safe_release_version,
        safe_timestamp,
        string,
        validated_benchmark_entry,
        version_sort_key,
    )
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    from release_benchmark_safety import (
        MAIN_SNAPSHOT_FALLBACK_KEYS,
        MAIN_SNAPSHOT_METADATA_KEYS,
        MAIN_VERSION,
        safe_incoming_metadata,
        safe_release_dates,
        safe_release_version,
        safe_timestamp,
        string,
        validated_benchmark_entry,
        version_sort_key,
    )

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASE_DATA = ROOT / "docs" / "data" / "release-benchmarks.json"
DEFAULT_INCOMING_DATA = ROOT / ".benchmarks" / "release-benchmarks.json"
DEFAULT_OUTPUT = DEFAULT_BASE_DATA

EntryKey = tuple[str, str, str, str]
MAIN_SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")


@dataclass(frozen=True, slots=True)
class HistoryMergeOptions:
    """Optional controls for one history merge."""

    selection_path: Path | None = None
    generated_at: str = ""
    current_main_sha: str = ""


DEFAULT_HISTORY_MERGE_OPTIONS = HistoryMergeOptions()


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _load_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _payload(path: Path) -> dict[str, object]:
    if not path.exists():
        return {"schema_version": 1, "metadata": {}, "entries": []}
    payload = _load_json(path)
    if isinstance(payload, dict):
        return payload
    msg = f"Benchmark data in {path} must be a JSON object"
    raise TypeError(msg)


def _metadata(payload: dict[str, object]) -> dict[str, object]:
    if isinstance(metadata := payload.get("metadata"), dict):
        return dict(metadata)
    return {}


def _entries(payload: dict[str, object], *, path: Path) -> list[dict[str, object]]:
    if isinstance(entries := payload.get("entries"), list):
        validated_entries: list[dict[str, object]] = []
        for index, entry in enumerate(entries, start=1):
            if not isinstance(entry, dict):
                msg = f"Benchmark entry #{index} in {path} must be a JSON object"
                raise TypeError(msg)
            validated_entries.append(validated_benchmark_entry(entry, path=path, index=index))
        return validated_entries
    msg = f"Benchmark data in {path} must contain an entries list"
    raise TypeError(msg)


def _entry_key(entry: dict[str, object]) -> EntryKey:
    return (
        str(entry.get("version", "")),
        str(entry.get("input_type", "")),
        str(entry.get("case", "")),
        str(entry.get("formatter", "")),
    )


def _entry_sort_key(entry: dict[str, object]) -> tuple[tuple[int, int, int, int, int, str], str, str, str]:
    version, input_type, case, formatter = _entry_key(entry)
    return version_sort_key(version), input_type, case, formatter


def _contains_main_snapshot(entries: list[dict[str, object]]) -> bool:
    """Return whether a benchmark payload includes the synthetic main snapshot."""
    return any(_entry_key(entry)[0] == MAIN_VERSION for entry in entries)


def _current_main_entries(
    entries: list[dict[str, object]], incoming_metadata: dict[str, object], current_main_sha: str
) -> list[dict[str, object]]:
    """Exclude a main snapshot when it no longer matches the repository's main SHA."""
    if not current_main_sha or not _contains_main_snapshot(entries):
        return entries
    if _string(incoming_metadata.get("main_snapshot_collector_sha")) == current_main_sha:
        return entries
    return [entry for entry in entries if _entry_key(entry)[0] != MAIN_VERSION]


def _merged_entries(
    base_entries: list[dict[str, object]], incoming_entries: list[dict[str, object]]
) -> list[dict[str, object]]:
    incoming_by_key = {_entry_key(entry): entry for entry in incoming_entries}
    replaces_main = _contains_main_snapshot(incoming_entries)
    merged_by_key: dict[EntryKey, dict[str, object]] = {}
    for entry in base_entries:
        if (entry_key := _entry_key(entry)) in incoming_by_key:
            continue
        if replaces_main and entry_key[0] == MAIN_VERSION:
            continue
        merged_by_key[entry_key] = entry
    merged_by_key.update(incoming_by_key)
    return sorted(merged_by_key.values(), key=_entry_sort_key)


def _string(value: object) -> str:
    return string(value)


def _int_string(value: object) -> str:
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str) and value.isdecimal():
        return value
    return ""


def _release_dates_from_metadata(metadata: dict[str, object]) -> dict[str, str]:
    if not isinstance(raw_dates := metadata.get("release_dates"), dict):
        return {}
    return safe_release_dates(raw_dates, path=DEFAULT_BASE_DATA)


def _release_dates_from_selection(path: Path | None) -> dict[str, str]:
    if path is None or not path.exists():
        return {}
    payload = _load_json(path)
    if not isinstance(payload, dict) or not isinstance(releases := payload.get("releases"), list):
        return {}
    release_dates: dict[str, str] = {}
    for index, release in enumerate(releases, start=1):
        if not isinstance(release, dict):
            continue
        version = safe_release_version(release.get("version"), path=path, field="version", index=index)
        uploaded_at = safe_timestamp(release.get("uploaded_at"), path=path, field="uploaded_at", index=index)
        if version and uploaded_at:
            release_dates[version] = uploaded_at
    return release_dates


def _merged_release_dates(
    base_metadata: dict[str, object],
    incoming_metadata: dict[str, object],
    selection_path: Path | None,
) -> dict[str, str]:
    release_dates = _release_dates_from_metadata(base_metadata)
    for version, uploaded_at in _release_dates_from_metadata(incoming_metadata).items():
        if uploaded_at or version not in release_dates:
            release_dates[version] = uploaded_at
    release_dates.update(_release_dates_from_selection(selection_path))
    return dict(sorted(release_dates.items(), key=lambda item: version_sort_key(item[0])))


def _unique_versions(entries: list[dict[str, object]]) -> list[str]:
    versions = {version for entry in entries if (version := _string(entry.get("version")).strip())}
    return sorted(versions, key=version_sort_key)


def _runs_per_case(entries: list[dict[str, object]], fallback: str) -> str:
    runs = {str(run) for entry in entries if isinstance(run := entry.get("runs"), int) and run > 0}
    if not runs:
        return fallback
    if len(runs) == 1:
        return next(iter(runs))
    return "mixed"


def _selection_strategy(base_metadata: dict[str, object], incoming_metadata: dict[str, object]) -> str:
    base_strategy = _string(base_metadata.get("selection_strategy"))
    incoming_strategy = _string(incoming_metadata.get("selection_strategy"))
    if not base_strategy:
        return incoming_strategy
    if incoming_strategy == "explicit":
        return base_strategy
    if base_strategy == incoming_strategy or not incoming_strategy:
        return base_strategy
    return f"{base_strategy}_with_{incoming_strategy}"


def _runner_os(incoming_metadata: dict[str, object]) -> str:
    return (
        _string(incoming_metadata.get("os"))
        or os.environ.get("OS")
        or os.environ.get("RUNNER_OS")
        or platform.platform()
    )


def _merged_metadata(
    base_metadata: dict[str, object],
    incoming_metadata: dict[str, object],
    entries: list[dict[str, object]],
    *,
    selection_path: Path | None,
    generated_at: str,
) -> dict[str, object]:
    safe_incoming = safe_incoming_metadata(incoming_metadata, path=DEFAULT_INCOMING_DATA)
    metadata: dict[str, object] = {
        **base_metadata,
        **{key: value for key, value in safe_incoming.items() if key not in MAIN_SNAPSHOT_METADATA_KEYS},
    }
    versions = _unique_versions(entries)
    fallback_runs = _string(safe_incoming.get("runs_per_case")) or _string(base_metadata.get("runs_per_case"))
    metadata.update({
        "generated_at": generated_at or _string(safe_incoming.get("generated_at")) or _timestamp(),
        "workflow": _string(safe_incoming.get("workflow")) or _string(base_metadata.get("workflow")),
        "workflow_run_id": _string(safe_incoming.get("workflow_run_id")),
        "collector_sha": _string(safe_incoming.get("collector_sha")),
        "os": _runner_os(safe_incoming),
        "python_version": _string(safe_incoming.get("python_version")) or _string(base_metadata.get("python_version")),
        "runs_per_case": _runs_per_case(entries, fallback_runs),
        "selection_strategy": _selection_strategy(base_metadata, safe_incoming),
        "selected_versions": ",".join(versions),
        "release_dates": _merged_release_dates(base_metadata, incoming_metadata, selection_path),
    })
    if last_month := _int_string(safe_incoming.get("pypistats_last_month")):
        metadata["pypistats_last_month"] = last_month
    if not _contains_main_snapshot(entries):
        for key in MAIN_SNAPSHOT_METADATA_KEYS:
            metadata.pop(key, None)
    return metadata


def _update_main_snapshot_metadata(metadata: dict[str, object], incoming_metadata: dict[str, object]) -> None:
    """Replace metadata describing the main snapshot in place."""
    safe_incoming = safe_incoming_metadata(incoming_metadata, path=DEFAULT_INCOMING_DATA)
    for key in MAIN_SNAPSHOT_METADATA_KEYS:
        if value := _string(safe_incoming.get(key)) or _string(safe_incoming.get(MAIN_SNAPSHOT_FALLBACK_KEYS[key])):
            metadata[key] = value
        else:
            metadata.pop(key, None)


def merge_release_benchmark_history(
    *,
    base_data_path: Path,
    incoming_data_path: Path,
    output_path: Path,
    options: HistoryMergeOptions = DEFAULT_HISTORY_MERGE_OPTIONS,
) -> dict[str, object]:
    """Merge incoming release benchmark data into committed historical data."""
    base_payload = _payload(base_data_path)
    if not incoming_data_path.exists():
        msg = f"Incoming benchmark data not found: {incoming_data_path}"
        raise FileNotFoundError(msg)
    incoming_payload = _payload(incoming_data_path)
    base_entries = _entries(base_payload, path=base_data_path)
    incoming_entries = _entries(incoming_payload, path=incoming_data_path)
    if options.current_main_sha and not MAIN_SHA_PATTERN.fullmatch(options.current_main_sha):
        msg = f"--current-main-sha must be a 40-character lowercase commit SHA, got {options.current_main_sha!r}"
        raise ValueError(msg)
    base_entries = _current_main_entries(base_entries, _metadata(base_payload), options.current_main_sha)
    incoming_entries = _current_main_entries(incoming_entries, _metadata(incoming_payload), options.current_main_sha)
    entries = _merged_entries(base_entries, incoming_entries)
    metadata = _merged_metadata(
        _metadata(base_payload),
        _metadata(incoming_payload),
        entries,
        selection_path=options.selection_path,
        generated_at=options.generated_at,
    )
    if _contains_main_snapshot(incoming_entries):
        _update_main_snapshot_metadata(metadata, _metadata(incoming_payload))
    payload = {
        "schema_version": incoming_payload.get("schema_version") or base_payload.get("schema_version") or 1,
        "metadata": metadata,
        "entries": entries,
    }
    _write_json(output_path, payload)
    return payload


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description="Merge release benchmark results into committed history")
    parser.add_argument("--base-data", type=Path, default=DEFAULT_BASE_DATA, help="Existing benchmark history JSON")
    parser.add_argument("--incoming-data", type=Path, default=DEFAULT_INCOMING_DATA, help="New benchmark JSON artifact")
    parser.add_argument("--selection", type=Path, default=None, help="Optional refreshed release selection JSON")
    parser.add_argument("--generated-at", default="", help="UTC generated_at override for deterministic tests")
    parser.add_argument(
        "--current-main-sha",
        default="",
        help="Discard an incoming main snapshot unless it matches this 40-character commit SHA",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="Merged benchmark history JSON output")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Script entrypoint."""
    args = parse_args(argv)
    merge_release_benchmark_history(
        base_data_path=args.base_data,
        incoming_data_path=args.incoming_data,
        output_path=args.output,
        options=HistoryMergeOptions(
            selection_path=args.selection,
            generated_at=args.generated_at,
            current_main_sha=args.current_main_sha,
        ),
    )
    print(f"Wrote {args.output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
