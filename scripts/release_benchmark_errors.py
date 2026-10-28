"""Helpers for public release benchmark error text."""

from __future__ import annotations

import re

MAX_PUBLIC_ERROR_LENGTH = 96

_BENCHMARK_TEMP_PATH_PATTERN = re.compile(
    r"(?:[A-Za-z]:)?[\\/][^\s'\",]*"
    r"(?:dcg-release-bench|datamodel-code-generator-release-bench)-[^\s'\",]*"
    r"|(?:dcg-release-bench|datamodel-code-generator-release-bench)-[^\s'\",]*",
)


def sanitize_benchmark_error(error: str) -> str:
    """Remove volatile benchmark worker paths from captured error output."""
    if not error:
        return ""
    return _BENCHMARK_TEMP_PATH_PATTERN.sub("<benchmark-temp>", error)


def compact_benchmark_error(error: str) -> str:
    """Normalize captured benchmark error output for JSON and docs."""
    return " ".join(sanitize_benchmark_error(error).split())


def summarize_benchmark_error(*, status: str, formatter: str, error: str) -> str:
    """Return a short public-facing error explanation."""
    if not (note := compact_benchmark_error(error)):
        return ""

    lower = note.lower()
    formatter_name = formatter.lower()
    summary = note
    match status:
        case "unsupported" if formatter_name and (
            "formatters" in lower or "unrecognized arguments" in lower or "invalid choice" in lower
        ):
            summary = "unavailable"
        case "failed" if "timed out after" in lower:
            summary = "timeout"
        case "failed" if "failed to build" in lower or "no matching distribution" in lower:
            summary = "install"
        case "failed" if "traceback" in lower:
            summary = "command"
        case _:
            pass

    if len(summary) <= MAX_PUBLIC_ERROR_LENGTH:
        return summary
    return f"{summary[: MAX_PUBLIC_ERROR_LENGTH - 3]}..."
