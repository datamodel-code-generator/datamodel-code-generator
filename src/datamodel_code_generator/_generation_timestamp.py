"""The generation timestamp: the time a file header names when its run pins none.

A run pins one timestamp on its configuration so every file it writes names the same time: a target run before it
generates its models (each later target of a batch takes the first one's), the CLI before ``--diff-against`` generates
both inputs, and the file header itself when nothing pinned one. This module imports only the standard library.
"""

from __future__ import annotations

from datetime import datetime, timezone


def current_timestamp() -> str:
    """Return the current UTC time in whole seconds as ISO 8601 text."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


__all__ = ["current_timestamp"]
