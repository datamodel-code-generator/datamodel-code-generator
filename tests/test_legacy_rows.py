"""Tests for the ``legacy-*`` rows that src/ names and the "Legacy rows" table of docs/architecture.md."""

from __future__ import annotations

import re
from pathlib import Path

from tests.conftest import assert_output

ROOT = Path(__file__).parents[1]
LEGACY_ROW = re.compile(r"legacy[-.][A-Za-z0-9_]+(?:[-.][A-Za-z0-9_]+)*(?![-\w])")
LISTED_ROW = re.compile(
    r"^\| `(legacy[-.][^`]+)` \| .* \| (?:#\d+|keep|[A-Z]{1,4}-?\d+[a-z]?(?:-\d+)?) \|$",
    re.MULTILINE,
)


def test_legacy_rows_are_listed_with_their_fix() -> None:
    """Every row src/ names is a table row with its fix (an issue, a plan group or keep), and no other row is named."""
    used = {
        row
        for path in (ROOT / "src" / "datamodel_code_generator").rglob("*.py")
        for row in LEGACY_ROW.findall(path.read_text(encoding="utf-8"))
    }
    docs = (ROOT / "docs" / "architecture.md").read_text(encoding="utf-8")
    listed = set(LISTED_ROW.findall(docs))
    report = [
        *(f"{row}: src/ names it; list it in the Legacy rows table with its fix" for row in sorted(used - listed)),
        *(
            f"{row}: docs/architecture.md names it; src/ does not"
            for row in sorted(set(LEGACY_ROW.findall(docs)) - used)
        ),
    ]

    assert_output("\n".join(report))
