"""Report fixed Claude failure categories without publishing execution content."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

MAX_EXECUTION_BYTES = 1_048_576
MAX_ERROR_CHARACTERS = 8_192
ERROR_CATEGORIES = {
    "authentication_failed": "authentication",
    "billing_error": "billing",
    "rate_limit": "rate_limit",
    "invalid_request": "invalid_request",
    "server_error": "server",
    "max_output_tokens": "output_limit",
    "model_context_window_exceeded": "context_limit",
}
ERROR_PATTERNS = (
    (
        "authentication",
        ("authentication_error", "invalid api key", "oauth token", "please run /login", "api error: 401"),
    ),
    ("billing", ("billing_error", "credit balance", "api error: 402")),
    ("rate_limit", ("rate_limit_error", "usage limit", "hit your limit", "api error: 429")),
    ("model_unavailable", ("model_not_found", "model not found", "model does not exist", "api error: 404")),
    ("permission", ("permission_error", "api error: 403")),
    ("server", ("overloaded_error", "api_error", "api error: 500", "api error: 529")),
    ("invalid_request", ("invalid_request_error", "api error: 400")),
)


def _classify_text(value: object) -> str | None:
    """Map a bounded error string to a fixed label; never return input text."""
    if not isinstance(value, str):
        return None
    text = value[:MAX_ERROR_CHARACTERS].lower()
    for category, patterns in ERROR_PATTERNS:
        if any(pattern in text for pattern in patterns):
            return category
    return None


def _failure_category(messages: list[object]) -> str:
    """Inspect failure frames only, ignoring prompts and tool results."""
    for message in reversed(messages):
        match message:
            case {"type": "assistant", "error": str(error)} if error in ERROR_CATEGORIES:
                return ERROR_CATEGORIES[error]
            case {"type": "result", "is_error": True}:
                if category := _classify_text(message.get("result")):
                    return category
                if isinstance(errors := message.get("errors"), list):
                    for error in errors:
                        if category := _classify_text(error):
                            return category
    return "unknown"


def _read_category(execution_path: Path) -> str:
    """Bound reads and suppress parser exceptions that may contain raw content."""
    try:
        with execution_path.open("rb") as execution_file:
            if (size := os.fstat(execution_file.fileno()).st_size) > MAX_EXECUTION_BYTES:
                return "record_too_large"
            raw = execution_file.read(size + 1)
        messages = json.loads(raw)
    except (OSError, ValueError, RecursionError):
        return "record_unavailable"
    if not isinstance(messages, list):
        return "record_invalid"
    return _failure_category(messages)


def main(argv: list[str] | None = None) -> None:
    """Print only a trusted category; keep the original failed step authoritative."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execution-path", required=True, type=Path)
    args = parser.parse_args(argv)
    print(f"Claude failure diagnostic: {_read_category(args.execution_path)} (raw output withheld).")


if __name__ == "__main__":
    main()
