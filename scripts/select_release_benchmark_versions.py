"""Select release versions for datamodel-code-generator release benchmarks.

Usage:
    python scripts/select_release_benchmark_versions.py --output .benchmarks/release-benchmark-selection.json
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import ssl
import sys
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    from scripts.release_benchmark_safety import MAIN_VERSION, safe_release_version
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    from release_benchmark_safety import MAIN_VERSION, safe_release_version

if importlib.util.find_spec("certifi"):
    import certifi
else:
    certifi = None

PACKAGE_NAME = "datamodel-code-generator"
PYPI_JSON_URL = f"https://pypi.org/pypi/{PACKAGE_NAME}/json"
PYPISTATS_RECENT_URL = f"https://pypistats.org/api/packages/{PACKAGE_NAME}/recent"
PYPISTATS_OVERALL_URL = f"https://pypistats.org/api/packages/{PACKAGE_NAME}/overall?mirrors=false"
CLICKPY_SQL_URL = "https://sql-clickhouse.clickhouse.com/?user=demo&wait_end_of_query=1"
CLICKPY_VERSION_USAGE_QUERY = """
SELECT
  version,
  sum(count) AS downloads,
  min(date) AS first_seen,
  max(date) AS last_seen
FROM pypi.pypi_downloads_per_day_by_version
WHERE project = '{package_name}'
  AND date >= today() - INTERVAL {history_days} DAY
GROUP BY version
ORDER BY downloads DESC
LIMIT {limit}
FORMAT JSON
"""
DEFAULT_OUTPUT = Path(".benchmarks") / "release-benchmark-selection.json"
DEFAULT_HISTORY_DAYS = 365
DEFAULT_LIMIT = 40
REQUEST_TIMEOUT_SECONDS = 30
PRE_RELEASE_PATTERN = re.compile(r"(a|b|rc|dev)", re.IGNORECASE)
AGE_BUCKETS = (
    (0, 30),
    (31, 90),
    (91, 180),
    (181, 365),
    (366, 10_000),
)


@dataclass(frozen=True, slots=True)
class ReleaseVersion:
    """PyPI release version and upload timestamp."""

    version: str
    uploaded_at: str
    downloads: int = 0
    first_seen: str = ""
    last_seen: str = ""


@dataclass(frozen=True, slots=True)
class VersionUsage:
    """Download usage for one release version."""

    version: str
    downloads: int
    first_seen: str
    last_seen: str


@dataclass(frozen=True, slots=True)
class VersionSelection:
    """Resolved benchmark version selection metadata."""

    schema_version: int
    generated_at: str
    package: str
    strategy: str
    history_days: int
    limit: int
    selected_versions: list[str]
    releases: list[ReleaseVersion]
    download_stats: dict[str, object]


@dataclass(frozen=True, slots=True)
class SelectionConfig:
    """Version selection inputs."""

    pypi_source: str | Path
    clickpy_source: str | Path | None
    recent_source: str | Path | None
    overall_source: str | Path | None
    explicit_versions: str
    history_days: int
    limit: int
    now: datetime
    include_main: bool = False


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _split_csv_words(raw: str) -> list[str]:
    return list(dict.fromkeys(token for part in raw.splitlines() for token in re.split(r"[\s,]+", part) if token))


def _normalize_version(version: str) -> str:
    normalized = version.strip().removeprefix("v")
    return safe_release_version(normalized, path=DEFAULT_OUTPUT, field="version")


def _safe_normalize_version(version: str) -> str:
    try:
        return _normalize_version(version)
    except ValueError:
        return ""


def _is_stable_version(version: str) -> bool:
    return PRE_RELEASE_PATTERN.search(version) is None


def _ssl_context() -> ssl.SSLContext | None:
    if certifi is None:
        return None
    return ssl.create_default_context(cafile=certifi.where())


def _read_json_url(url: str) -> object:
    request = urllib.request.Request(url, headers={"User-Agent": f"{PACKAGE_NAME} release benchmark selector"})
    with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS, context=_ssl_context()) as response:
        return json.loads(response.read().decode())


def _read_clickpy_json(history_days: int, limit: int) -> object:
    query = CLICKPY_VERSION_USAGE_QUERY.format(
        package_name=PACKAGE_NAME,
        history_days=history_days,
        limit=max(limit * 4, 100),
    )
    request = urllib.request.Request(
        CLICKPY_SQL_URL,
        data=query.encode(),
        headers={
            "Content-Type": "text/plain; charset=utf-8",
            "User-Agent": f"{PACKAGE_NAME} release benchmark selector",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS, context=_ssl_context()) as response:
        return json.loads(response.read().decode())


def _read_json(path_or_url: str | Path | None) -> object:
    if path_or_url is None:
        msg = "Missing JSON source"
        raise ValueError(msg)
    source = str(path_or_url)
    if source.startswith(("https://", "http://")):
        return _read_json_url(source)
    return json.loads(Path(source).read_text(encoding="utf-8"))


def _release_versions(payload: object) -> list[ReleaseVersion]:
    if not isinstance(payload, dict) or not isinstance(releases := payload.get("releases"), dict):
        msg = "PyPI JSON must contain a releases object"
        raise TypeError(msg)

    versions: list[ReleaseVersion] = []
    for version, files in releases.items():
        if not isinstance(version, str) or not isinstance(files, list) or not _is_stable_version(version):
            continue
        if not (normalized_version := _safe_normalize_version(version)):
            continue
        active_files = [file_info for file_info in files if isinstance(file_info, dict) and not file_info.get("yanked")]
        if not active_files:
            continue
        upload_times = [
            _parse_datetime(uploaded_at)
            for file_info in active_files
            if isinstance(file_info, dict) and isinstance(uploaded_at := file_info.get("upload_time_iso_8601"), str)
        ]
        if not upload_times:
            continue
        versions.append(ReleaseVersion(version=normalized_version, uploaded_at=_timestamp(max(upload_times))))
    return sorted(versions, key=lambda release: _parse_datetime(release.uploaded_at), reverse=True)


def _version_usage(payload: object) -> list[VersionUsage]:
    if not isinstance(payload, dict) or not isinstance(rows := payload.get("data"), list):
        msg = "ClickPy JSON must contain a data list"
        raise TypeError(msg)
    usage: list[VersionUsage] = []
    for row in rows:
        if not isinstance(row, dict) or not isinstance(version := row.get("version"), str):
            continue
        if not (normalized_version := _safe_normalize_version(version)):
            continue
        usage.append(
            VersionUsage(
                version=normalized_version,
                downloads=int(row.get("downloads", 0)),
                first_seen=str(row.get("first_seen", "")),
                last_seen=str(row.get("last_seen", "")),
            )
        )
    return sorted(usage, key=lambda item: item.downloads, reverse=True)


def _release_with_usage(release: ReleaseVersion, usage: VersionUsage | None) -> ReleaseVersion:
    if usage is None:
        return release
    return ReleaseVersion(
        version=release.version,
        uploaded_at=release.uploaded_at,
        downloads=usage.downloads,
        first_seen=usage.first_seen,
        last_seen=usage.last_seen,
    )


def _latest_releases(releases: list[ReleaseVersion], count: int = 2) -> list[ReleaseVersion]:
    return releases[:count]


def _age_bucket_index(release: ReleaseVersion, *, now: datetime) -> int:
    age_days = (now - _parse_datetime(release.uploaded_at)).days
    for index, (start, end) in enumerate(AGE_BUCKETS):
        if start <= age_days <= end:
            return index
    return len(AGE_BUCKETS)


def _append_unique(selected: list[ReleaseVersion], release: ReleaseVersion, *, limit: int) -> None:
    if len(selected) >= limit or release.version in {item.version for item in selected}:
        return
    selected.append(release)


def _selected_explicit_releases(
    releases_by_version: dict[str, ReleaseVersion],
    usage_by_version: dict[str, VersionUsage],
    explicit_versions: str,
    *,
    limit: int,
) -> list[ReleaseVersion]:
    return [
        _release_with_usage(
            releases_by_version.get(version) or ReleaseVersion(version=version, uploaded_at=""),
            usage_by_version.get(version),
        )
        for raw_version in _split_csv_words(explicit_versions)
        if (version := _safe_normalize_version(raw_version)) and version != MAIN_VERSION
    ][:limit]


def _explicit_requests_main(explicit_versions: str) -> bool:
    return any(_safe_normalize_version(version) == MAIN_VERSION for version in _split_csv_words(explicit_versions))


def _is_main_only_request(explicit_versions: str) -> bool:
    """Return whether explicit input contains only the synthetic main version."""
    if not (versions := _split_csv_words(explicit_versions)):
        return False
    return all(_safe_normalize_version(version) == MAIN_VERSION for version in versions)


def _main_only_selection(config: SelectionConfig) -> VersionSelection:
    """Build selection metadata without reading external release or download sources."""
    return VersionSelection(
        schema_version=1,
        generated_at=_timestamp(config.now),
        package=PACKAGE_NAME,
        strategy="explicit",
        history_days=config.history_days,
        limit=config.limit,
        selected_versions=[MAIN_VERSION],
        releases=[],
        download_stats={},
    )


def _selected_usage_releases(
    releases: list[ReleaseVersion],
    usage: list[VersionUsage],
    *,
    limit: int,
    now: datetime,
) -> list[ReleaseVersion]:
    releases_by_version = {release.version: release for release in releases}
    usage_by_version = {item.version: item for item in usage}
    selected: list[ReleaseVersion] = []

    for release in _latest_releases(releases):
        _append_unique(selected, _release_with_usage(release, usage_by_version.get(release.version)), limit=limit)

    by_bucket: dict[int, list[ReleaseVersion]] = {}
    for usage_item in usage:
        if release := releases_by_version.get(usage_item.version):
            by_bucket.setdefault(_age_bucket_index(release, now=now), []).append(
                _release_with_usage(release, usage_item)
            )
    for bucket in sorted(by_bucket):
        if by_bucket[bucket]:
            _append_unique(selected, by_bucket[bucket][0], limit=limit)

    for usage_item in usage:
        if release := releases_by_version.get(usage_item.version):
            _append_unique(selected, _release_with_usage(release, usage_item), limit=limit)

    for release in releases:
        _append_unique(selected, _release_with_usage(release, usage_by_version.get(release.version)), limit=limit)
    return selected


def _selected_recent_releases(
    releases: list[ReleaseVersion],
    *,
    history_days: int,
    limit: int,
    now: datetime,
) -> list[ReleaseVersion]:
    cutoff = now - timedelta(days=history_days)
    selected = [release for release in releases if _parse_datetime(release.uploaded_at) >= cutoff]
    return selected[:limit]


def _pypistats_recent(payload: object) -> dict[str, object]:
    if not isinstance(payload, dict) or not isinstance(data := payload.get("data"), dict):
        return {}
    return {
        "last_day": data.get("last_day", ""),
        "last_week": data.get("last_week", ""),
        "last_month": data.get("last_month", ""),
    }


def _pypistats_overall(payload: object) -> dict[str, object]:
    if not isinstance(payload, dict) or not isinstance(rows := payload.get("data"), list):
        return {}
    downloads = [row.get("downloads", 0) for row in rows if isinstance(row, dict)]
    return {
        "timeseries_days": len(downloads),
        "timeseries_downloads": sum(value for value in downloads if isinstance(value, int)),
    }


def _load_pypistats(recent_source: str | Path | None, overall_source: str | Path | None) -> dict[str, object]:
    stats: dict[str, object] = {"source": "pypistats.org"}
    try:
        stats.update(_pypistats_recent(_read_json(recent_source or PYPISTATS_RECENT_URL)))
        stats.update(_pypistats_overall(_read_json(overall_source or PYPISTATS_OVERALL_URL)))
    except (OSError, ValueError, TypeError, urllib.error.URLError) as exc:
        stats["error"] = str(exc)
    return stats


def _load_clickpy_usage(
    source: str | Path | None, *, history_days: int, limit: int
) -> tuple[list[VersionUsage], dict[str, object]]:
    stats: dict[str, object] = {"source": "clickpy", "window_days": str(history_days)}
    try:
        payload = _read_json(source) if source else _read_clickpy_json(history_days, limit)
        usage = _version_usage(payload)
        stats.update({
            "versions_with_downloads": str(len(usage)),
            "window_downloads": str(sum(item.downloads for item in usage)),
        })
    except (OSError, ValueError, TypeError, urllib.error.URLError) as exc:
        stats["error"] = str(exc)
        return [], stats
    else:
        return usage, stats


def select_versions(config: SelectionConfig) -> VersionSelection:
    """Resolve benchmark release versions and package download metadata."""
    if _is_main_only_request(config.explicit_versions):
        return _main_only_selection(config)

    releases = _release_versions(_read_json(config.pypi_source))
    usage, clickpy_stats = _load_clickpy_usage(
        config.clickpy_source,
        history_days=config.history_days,
        limit=config.limit,
    )
    usage_by_version = {item.version: item for item in usage}
    releases_by_version = {release.version: release for release in releases}
    if config.explicit_versions:
        strategy = "explicit"
        selected = _selected_explicit_releases(
            releases_by_version,
            usage_by_version,
            config.explicit_versions,
            limit=config.limit,
        )
    elif usage:
        strategy = "clickpy_downloads"
        selected = _selected_usage_releases(releases, usage, limit=config.limit, now=config.now)
    else:
        strategy = "release_upload_time"
        selected = _selected_recent_releases(
            releases,
            history_days=config.history_days,
            limit=config.limit,
            now=config.now,
        )
    download_stats = {
        **clickpy_stats,
        "pypistats": _load_pypistats(config.recent_source, config.overall_source),
    }
    selected_versions = [release.version for release in selected]
    if config.include_main or _explicit_requests_main(config.explicit_versions):
        selected_versions.append(MAIN_VERSION)
    return VersionSelection(
        schema_version=1,
        generated_at=_timestamp(config.now),
        package=PACKAGE_NAME,
        strategy=strategy,
        history_days=config.history_days,
        limit=config.limit,
        selected_versions=selected_versions,
        releases=selected,
        download_stats=download_stats,
    )


def _selection_payload(selection: VersionSelection) -> dict[str, object]:
    return asdict(selection)


def _write_github_outputs(selection: VersionSelection) -> None:
    if not (output_path := os.environ.get("GITHUB_OUTPUT")):
        return
    with Path(output_path).open("a", encoding="utf-8") as output:
        output.write(f"versions_json={json.dumps(selection.selected_versions, separators=(',', ':'))}\n")
        output.write(f"versions={','.join(selection.selected_versions)}\n")


def _positive_int_error(name: str, value: int) -> str:
    if value > 0:
        return ""
    return f"{name} must be greater than 0, got {value}"


def _validate_args(args: argparse.Namespace) -> str:
    errors = [
        error
        for name, value in (("--history-days", args.history_days), ("--limit", args.limit))
        if (error := _positive_int_error(name, value))
    ]
    if not errors:
        return ""
    return "\n".join(errors)


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description="Select datamodel-code-generator release benchmark versions")
    parser.add_argument("--versions", default="", help="Comma, whitespace, or newline separated release versions/tags")
    parser.add_argument("--history-days", type=int, default=DEFAULT_HISTORY_DAYS, help="Release upload lookback window")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help="Maximum selected release versions")
    parser.add_argument(
        "--include-main",
        action="store_true",
        help="Append the current main branch snapshot without counting it against the release limit",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="Selection JSON output path")
    parser.add_argument("--pypi-json", default=PYPI_JSON_URL, help="PyPI project JSON URL or fixture path")
    parser.add_argument("--clickpy-json", default=None, help="ClickPy version-download JSON fixture path")
    parser.add_argument("--pypistats-recent-json", default=None, help="PyPIStats recent JSON URL or fixture path")
    parser.add_argument("--pypistats-overall-json", default=None, help="PyPIStats overall JSON URL or fixture path")
    parser.add_argument("--now", default="", help="UTC timestamp override for deterministic tests")
    return parser.parse_args()


def main() -> int:
    """Script entrypoint."""
    args = parse_args()
    if error := _validate_args(args):
        print(error, file=sys.stderr)
        return 2

    now = _parse_datetime(args.now) if args.now else _utc_now()
    selection = select_versions(
        SelectionConfig(
            pypi_source=args.pypi_json,
            clickpy_source=args.clickpy_json,
            recent_source=args.pypistats_recent_json,
            overall_source=args.pypistats_overall_json,
            explicit_versions=args.versions,
            history_days=args.history_days,
            limit=args.limit,
            now=now,
            include_main=args.include_main,
        )
    )
    if not selection.selected_versions:
        print("No release versions selected", file=sys.stderr)
        return 1

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(_selection_payload(selection), indent=2) + "\n", encoding="utf-8")
    _write_github_outputs(selection)
    print(",".join(selection.selected_versions))
    return 0


if __name__ == "__main__":
    sys.exit(main())
