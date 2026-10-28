"""Merge per-version release benchmark JSON files.

Usage:
    python scripts/merge_release_benchmarks.py .benchmarks/parts/*.json
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from scripts.release_benchmark_errors import compact_benchmark_error
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    from release_benchmark_errors import compact_benchmark_error

try:
    from scripts.release_benchmark_safety import MAIN_SNAPSHOT_FALLBACK_KEYS, MAIN_SNAPSHOT_METADATA_KEYS, MAIN_VERSION
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    from release_benchmark_safety import MAIN_SNAPSHOT_FALLBACK_KEYS, MAIN_SNAPSHOT_METADATA_KEYS, MAIN_VERSION

DEFAULT_OUTPUT = Path(".benchmarks") / "release-benchmarks.json"
DEFAULT_SELECTION = Path(".benchmarks") / "release-benchmark-selection.json"


def _load_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def _metadata(payload: object) -> dict[str, Any]:
    if isinstance(payload, dict) and isinstance(metadata := payload.get("metadata"), dict):
        return metadata
    return {}


def _entries(payload: object, *, path: Path) -> list[dict[str, object]]:
    if not isinstance(payload, dict) or not isinstance(entries := payload.get("entries"), list):
        msg = f"Benchmark fragment {path} must contain an entries list"
        raise TypeError(msg)
    return [_entry_payload(entry) for entry in entries if isinstance(entry, dict)]


def _main_snapshot_metadata(entries: list[dict[str, object]], metadata: dict[str, Any]) -> dict[str, object]:
    if not any(entry.get("version") == MAIN_VERSION for entry in entries):
        return {}
    return {
        key: value
        for key in MAIN_SNAPSHOT_METADATA_KEYS
        if (value := metadata.get(key, metadata.get(MAIN_SNAPSHOT_FALLBACK_KEYS[key])))
    }


def _entry_payload(entry: dict[str, object]) -> dict[str, object]:
    payload = dict(entry)
    if not isinstance(error := payload.get("error"), str):
        return payload
    payload["error"] = compact_benchmark_error(error)
    return payload


def _selection_metadata(selection: object) -> dict[str, object]:
    if not isinstance(selection, dict):
        return {}
    download_stats = selection.get("download_stats")
    if not isinstance(download_stats, dict):
        download_stats = {}
    pypistats = download_stats.get("pypistats")
    if not isinstance(pypistats, dict):
        pypistats = {}
    selected_versions = selection.get("selected_versions")
    if not isinstance(selected_versions, list):
        selected_versions = []
    return {
        "selection_strategy": selection.get("strategy", ""),
        "selection_window_days": str(selection.get("history_days", "")),
        "selected_versions": ",".join(str(version) for version in selected_versions),
        "download_source": download_stats.get("source", ""),
        "download_window_days": str(download_stats.get("window_days", "")),
        "download_versions": str(download_stats.get("versions_with_downloads", "")),
        "download_window_total": str(download_stats.get("window_downloads", "")),
        "pypistats_source": pypistats.get("source", ""),
        "pypistats_last_day": str(pypistats.get("last_day", "")),
        "pypistats_last_week": str(pypistats.get("last_week", "")),
        "pypistats_last_month": str(pypistats.get("last_month", "")),
        "pypistats_timeseries_days": str(pypistats.get("timeseries_days", "")),
        "pypistats_timeseries_downloads": str(pypistats.get("timeseries_downloads", "")),
        "release_dates": _selection_release_dates(selection),
    }


def _selection_release_dates(selection: dict[str, object]) -> dict[str, str]:
    if not isinstance(releases := selection.get("releases"), list):
        return {}
    release_dates: dict[str, str] = {}
    for release in releases:
        if (
            isinstance(release, dict)
            and isinstance(version := release.get("version"), str)
            and isinstance(uploaded_at := release.get("uploaded_at"), str)
        ):
            release_dates[version] = uploaded_at
    return release_dates


def _runner_os() -> str:
    return os.environ.get("OS") or os.environ.get("RUNNER_OS") or platform.platform()


def _python_version() -> str:
    return os.environ.get("BENCHMARK_PYTHON_VERSION") or platform.python_version()


def _payload(entries: list[dict[str, object]], *, runs: int, warmups: int) -> dict[str, object]:
    return {
        "schema_version": 1,
        "metadata": {
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "workflow": os.environ.get("GITHUB_WORKFLOW", "local"),
            "workflow_run_id": os.environ.get("GITHUB_RUN_ID", ""),
            "collector_sha": os.environ.get("GITHUB_SHA", ""),
            "os": _runner_os(),
            "python_version": _python_version(),
            "runs_per_case": str(runs),
            "warmups_per_case": str(warmups),
        },
        "entries": entries,
    }


def merge_benchmark_payloads(
    fragment_paths: list[Path],
    *,
    selection_path: Path,
    generated_at: str = "",
) -> dict[str, object]:
    """Merge benchmark result fragments and selection metadata."""
    entries: list[dict[str, object]] = []
    fragment_metadata: list[dict[str, Any]] = []
    main_snapshot_metadata: dict[str, object] = {}
    for path in fragment_paths:
        payload = _load_json(path)
        fragment_entries = _entries(payload, path=path)
        metadata = _metadata(payload)
        entries.extend(fragment_entries)
        fragment_metadata.append(metadata)
        main_snapshot_metadata.update(_main_snapshot_metadata(fragment_entries, metadata))

    selection = _load_json(selection_path) if selection_path.exists() else {}
    runs = next((metadata.get("runs_per_case") for metadata in fragment_metadata if metadata.get("runs_per_case")), "0")
    warmups = next(
        (metadata.get("warmups_per_case") for metadata in fragment_metadata if metadata.get("warmups_per_case")),
        "0",
    )
    payload = _payload(entries, runs=int(str(runs) or "0"), warmups=int(str(warmups) or "0"))
    payload_metadata = payload["metadata"]
    if isinstance(payload_metadata, dict):
        payload_metadata.update(_selection_metadata(selection))
        payload_metadata.update(main_snapshot_metadata)
        if generated_at:
            payload_metadata["generated_at"] = generated_at
        payload_metadata["workflow"] = os.environ.get("GITHUB_WORKFLOW", payload_metadata.get("workflow", "local"))
        payload_metadata["workflow_run_id"] = os.environ.get("GITHUB_RUN_ID", "")
        payload_metadata["collector_sha"] = os.environ.get("GITHUB_SHA", "")
        payload_metadata["os"] = _runner_os()
        payload_metadata["python_version"] = _python_version()
    return payload


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description="Merge datamodel-code-generator release benchmark fragments")
    parser.add_argument("fragments", nargs="+", type=Path, help="Per-version benchmark JSON fragments")
    parser.add_argument(
        "--selection", type=Path, default=DEFAULT_SELECTION, help="Release selection metadata JSON path"
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="Merged benchmark JSON output path")
    parser.add_argument("--generated-at", default="", help="UTC generated_at override for deterministic tests")
    return parser.parse_args()


def main() -> int:
    """Script entrypoint."""
    args = parse_args()
    fragments = sorted(args.fragments)
    if not fragments:
        print("No benchmark fragments provided", file=sys.stderr)
        return 2
    payload = merge_benchmark_payloads(fragments, selection_path=args.selection, generated_at=args.generated_at)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {args.output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
