"""Tests for the ``legacy-*`` rows that src/ names and their records in scripts/legacy_rows.py."""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from datamodel_code_generator.cli_options import get_all_argparse_options
from scripts.build_architecture_docs import LEGACY_BEGIN_MARKER, LEGACY_END_MARKER, generate_legacy_table
from scripts.legacy_row_reads import ROW, reading_lines, run_witness, write_reading_lines
from scripts.legacy_rows import KEEP, LEGACY_ROWS, LegacyRow

if TYPE_CHECKING:
    from collections.abc import Sequence

ROOT = Path(__file__).parents[1]
FLAGS = get_all_argparse_options()
WITNESSES = tuple(dict.fromkeys(row.witness for row in LEGACY_ROWS))
"""Every witness once, in the order of the rows."""


@pytest.mark.allow_direct_assert("an invariant of the records must fail the test, not be stored as an expected output")
def test_legacy_rows_are_the_rows_src_names() -> None:
    """Every row src/ names has one record in scripts/legacy_rows.py, and every record is a row src/ names."""
    used = {
        row
        for path in (ROOT / "src" / "datamodel_code_generator").rglob("*.py")
        for row in ROW.findall(path.read_text(encoding="utf-8"))
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
        (bool(reading_lines().get(row.name)), "src/ names it on no line that reads it"),
    )
    return [f"{row.name}: {message}" for holds, message in checks if not holds]


@pytest.mark.allow_direct_assert("an invariant of the records must fail the test, not be stored as an expected output")
def test_legacy_row_records_are_complete() -> None:
    """A record's fix is keep or an issue, its option is a command-line option, and its witness runs an input."""
    problems = [problem for row in LEGACY_ROWS for problem in _problems(row)]

    assert not problems, "\n".join(problems)


@pytest.mark.allow_direct_assert("an invariant of the records must fail the test, not be stored as an expected output")
def test_the_page_table_is_written_from_the_records() -> None:
    """The "Legacy rows" table of docs/architecture.md is the table the records write, row for row."""
    docs = (ROOT / "docs" / "architecture.md").read_text(encoding="utf-8")
    written = f"{LEGACY_BEGIN_MARKER}{generate_legacy_table()}{LEGACY_END_MARKER}"

    assert written in docs, "docs/architecture.md: run python scripts/build_architecture_docs.py"


@pytest.fixture(scope="module")
def reading_lines_file(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Write the reading lines of the rows once for the Pythons the witnesses run in."""
    path = tmp_path_factory.mktemp("legacy-rows") / "reading-lines.json"
    write_reading_lines(path)
    return path


def _witness_problems(rows: Sequence[LegacyRow], directory: Path, lines: Path) -> list[str]:
    """Run the witness the rows share once, and once more with each row's option; return what fails.

    The witness runs a line that reads each row, or writes the copied code that names it
    (``scripts/legacy_row_reads.py``), and each option changes its exit status, files or printed text.
    """
    witness = rows[0].witness
    plain = run_witness(witness, directory / "witness", lines)
    problems = [f"{row.name}: its witness runs no line that reads it" for row in rows if row.name not in plain.read]
    for index, row in enumerate(row for row in rows if row.option is not None):
        if run_witness([*witness, str(row.option)], directory / f"option-{index}", lines).output == plain.output:
            problems.append(f"{row.name}: its witness writes the same with {row.option}")
    return problems


@pytest.mark.skipif(sys.version_info < (3, 12), reason="sys.monitoring records the lines a run executes from 3.12")
@pytest.mark.parametrize(
    "witness", WITNESSES, ids=[next(row.name for row in LEGACY_ROWS if row.witness == w) for w in WITNESSES]
)
@pytest.mark.allow_direct_assert("an invariant of the records must fail the test, not be stored as an expected output")
def test_legacy_row_witnesses(witness: tuple[str, ...], tmp_path: Path, reading_lines_file: Path) -> None:
    """A witness runs a line that reads each row it is the witness of, and each such row's option changes its output."""
    problems = _witness_problems([row for row in LEGACY_ROWS if row.witness == witness], tmp_path, reading_lines_file)

    assert not problems, "\n".join(problems)


SEEDED_WITNESS = next(row.witness for row in LEGACY_ROWS if row.name == "legacy-exact-ints-as-floats")
SEEDED_ROWS = (
    LegacyRow(
        "legacy-msgspec-float-bounds", "seeded: a msgspec row on Pydantic output", fix=KEEP, witness=SEEDED_WITNESS
    ),
    LegacyRow(
        "legacy-exact-ints-as-floats",
        "seeded: an option of msgspec output on Pydantic output",
        fix=KEEP,
        witness=SEEDED_WITNESS,
        option="--read-msgspec-union-bounds-exactly",
    ),
)
"""Records with a witness that runs no line of the row, and with an option that changes nothing on the witness."""


@pytest.mark.skipif(sys.version_info < (3, 12), reason="sys.monitoring records the lines a run executes from 3.12")
@pytest.mark.allow_direct_assert("the problems of seeded records are the check's verdict, not a stored output")
def test_legacy_row_witness_check_fails_on_seeded_records(tmp_path: Path, reading_lines_file: Path) -> None:
    """A witness that runs no line of its row, or an option that changes nothing on the witness, is a problem."""
    problems = _witness_problems(SEEDED_ROWS, tmp_path, reading_lines_file)

    assert problems == [
        "legacy-msgspec-float-bounds: its witness runs no line that reads it",
        "legacy-exact-ints-as-floats: its witness writes the same with --read-msgspec-union-bounds-exactly",
    ]
