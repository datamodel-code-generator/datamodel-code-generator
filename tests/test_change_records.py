"""Tests for the pure helpers of the change records: the stamp-pending rule and the `covers` block of a fragment."""

from __future__ import annotations

import json

from scripts._change_records import stamp_pending, with_covers
from tests.conftest import assert_output

FRAGMENT = '[[change]]\nid = "x"\ncategory = "harness"\ntitle = "t"\nsummary = "s"\n'
FRAGMENTS = {
    "no covers yet": FRAGMENT,
    "no newline at the end": FRAGMENT.rstrip("\n"),
    "covers replaced": FRAGMENT + 'covers = [\n    "old.txt",\n]\n',
    "a sub-table": '[[change]]\nid = "x"\nkind = "output-text"\nsource = "run"\n\n[change.verify]\nschema = "{}"\n',
    "a header-like line in a string": '[[change]]\nid = "x"\nsummary = """\n[not a table]\n"""\n',
    "a comment first": '# moved\n\n[[change]]\nid = "x"\n',
    "covers in another form": FRAGMENT + 'covers = ["old.txt"]\n',
}
LABELS = {"labels": ['main/b "quoted".py', "main/a.py", "main/a.py", "main/é/"], "no labels": []}
STAMPS = {
    "listed": ("0.97.0", ["0.96.0", "0.97.0"]),
    "older tag": ("0.96.0", ["0.96.0", "0.97.0"]),
    "tag newer than every row": ("0.98.0", ["0.96.0", "0.97.0"]),
    "no rows": ("0.1.0", []),
}


def _covered(text: str, labels: list[str]) -> str:
    try:
        return with_covers(text, labels)
    except ValueError as error:
        return f"ValueError: {error}\n"


def test_change_record_helpers() -> None:
    """A `covers` block is written after the keys, before a sub-table, or after the header where a string hides one.

    Labels are sorted, unique and JSON-quoted; no labels drop the block; a hand-written `covers` is refused. A tag
    newer than every row of the release table is a pending stamp.
    """
    output = [
        f"=== {fragment}, {labels} ===\n{_covered(text, values)}"
        for fragment, text in FRAGMENTS.items()
        for labels, values in LABELS.items()
    ]
    output.append(json.dumps({case: stamp_pending(*args) for case, args in STAMPS.items()}, indent=1))
    assert_output("\n".join(output))
