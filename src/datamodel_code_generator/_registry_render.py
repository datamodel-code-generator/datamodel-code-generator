"""Private rendering helpers for registry modules."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence


def _render_registry_json(entries: Iterable[Mapping[str, object]]) -> str:
    """Render registry entries as stable JSON."""
    return json.dumps(list(entries), indent=2, sort_keys=True)


def _render_registry_table(rows: Sequence[Sequence[str]]) -> str:
    """Render registry rows as a stable plain text table."""
    widths = [max(len(row[index]) for row in rows) for index in range(len(rows[0]))]
    lines = [
        "  ".join(value.ljust(widths[index]) for index, value in enumerate(rows[0])).rstrip(),
        "  ".join("-" * width for width in widths),
    ]
    lines.extend("  ".join(value.ljust(widths[index]) for index, value in enumerate(row)).rstrip() for row in rows[1:])
    return "\n".join(lines) + "\n"
