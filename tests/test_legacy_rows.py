"""Tests for the ``legacy-*`` rows that src/ names and their records in scripts/legacy_rows.py."""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

from datamodel_code_generator.cli_options import get_all_argparse_options
from scripts.build_architecture_docs import LEGACY_BEGIN_MARKER, LEGACY_END_MARKER, generate_legacy_table
from scripts.legacy_rows import KEEP, LEGACY_ROWS, LegacyRow

ROOT = Path(__file__).parents[1]
LEGACY_ROW = re.compile(r"legacy[-.][A-Za-z0-9_]+(?:[-.][A-Za-z0-9_]+)*(?![-\w])")
FLAGS = get_all_argparse_options()


def test_legacy_rows_are_the_rows_src_names() -> None:
    """Every row src/ names has one record in scripts/legacy_rows.py, and every record is a row src/ names."""
    used = {
        row
        for path in (ROOT / "src" / "datamodel_code_generator").rglob("*.py")
        for row in LEGACY_ROW.findall(path.read_text(encoding="utf-8"))
    }
    recorded = Counter(row.name for row in LEGACY_ROWS)
    report = [
        *(f"{row}: src/ names it; add its record to scripts/legacy_rows.py" for row in sorted(used - set(recorded))),
        *(f"{row}: scripts/legacy_rows.py records it; src/ does not name it" for row in sorted(set(recorded) - used)),
        *(f"{row}: scripts/legacy_rows.py records it {count} times" for row, count in recorded.items() if count > 1),
    ]

    assert not report, "\n".join(report)


def _arguments(witness: tuple[str, ...]) -> dict[str, str]:
    """Return the option values of a witness, its ``--option value`` and ``--option=value`` spellings alike."""
    arguments: dict[str, str] = {}
    for index, token in enumerate(witness):
        if token.startswith("--"):
            option, equals, value = token.partition("=")
            following = witness[index + 1] if index + 1 < len(witness) else ""
            arguments[option] = value if equals else "" if following.startswith("--") else following
    return arguments


def _witness_input(witness: tuple[str, ...]) -> Path | None:
    """Return the file or directory under tests/data that a witness reads: its ``--input``, or its ``--input-model``."""
    arguments = _arguments(witness)
    model = arguments.get("--input-model", "").partition(":")[0]
    path = ROOT / arguments["--input"] if "--input" in arguments else ROOT / f"{model.replace('.', '/')}.py"
    data = ROOT / "tests" / "data"
    return path if (model or "--input" in arguments) and path.resolve().is_relative_to(data) and path.exists() else None


def _problems(row: LegacyRow) -> list[str]:
    """Return what is wrong with a record: its fix, its option or its witness."""
    checks = (
        (
            row.fix == KEEP or (isinstance(row.fix, int) and row.fix > 0),
            f"the fix {row.fix!r} is neither keep nor an issue",
        ),
        (row.option is None or row.option in FLAGS, f"the option {row.option} is no command-line option"),
        (_witness_input(row.witness) is not None, "the witness reads no input under tests/data"),
        ("--output" not in _arguments(row.witness), "the witness names --output"),
        (row.option is None or row.option not in _arguments(row.witness), "the witness turns the row's option on"),
    )
    return [f"{row.name}: {message}" for holds, message in checks if not holds]


def test_legacy_row_records_are_complete() -> None:
    """A record's fix is keep or an issue, its option is a command-line option, and its witness runs an input."""
    problems = [problem for row in LEGACY_ROWS for problem in _problems(row)]

    assert not problems, "\n".join(problems)


def test_the_page_table_is_written_from_the_records() -> None:
    """The "Legacy rows" table of docs/architecture.md is the table the records write, row for row."""
    docs = (ROOT / "docs" / "architecture.md").read_text(encoding="utf-8")
    written = f"{LEGACY_BEGIN_MARKER}{generate_legacy_table()}{LEGACY_END_MARKER}"

    assert written in docs, "docs/architecture.md: run python scripts/build_architecture_docs.py"
