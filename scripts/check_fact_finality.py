"""Check the pass declarations, and that the known finality violations still happen, so that ``KNOWN_WRITES`` shrinks.

Every run checks ``PASS_DECLS`` in ``src/datamodel_code_generator/parser/_pass_decls.py``: names are unique, phases
never go back in execution order, a repair names the change that removes it, and setup, check and bookkeeping passes
write no fact (a check pass may write ``STDERR``, the warnings it emits). ``KNOWN_WRITES`` in
``src/datamodel_code_generator/parser/_finality.py`` lists the violations of today's pipeline that
``DATAMODEL_CODE_GENERATOR_CHECK_FACT_FINALITY`` reports but never raises. Each entry names a
witness, the arguments of one ``datamodel-codegen`` run that makes it. ``--witnesses`` runs every witness with the check
reporting and lists the entries whose witness no longer makes their violation: the change that removed it removes the
entry too.

Usage:
    python scripts/check_fact_finality.py [--witnesses]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
VARIABLE = "DATAMODEL_CODE_GENERATOR_CHECK_FACT_FINALITY"
OTHER_AREAS = ("DATAMODEL_CODE_GENERATOR_CHECK_STORE_WRITES", "DATAMODEL_CODE_GENERATOR_CHECK_SCHEMA_LOCATIONS")

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from datamodel_code_generator.parser._facts import RenderFact  # noqa: E402, PLC2701
from datamodel_code_generator.parser._finality import KNOWN_WRITES  # noqa: E402, PLC2701
from datamodel_code_generator.parser._pass_decls import PASS_DECLS, PassKind  # noqa: E402, PLC2701

if TYPE_CHECKING:
    from collections.abc import Iterable

    from datamodel_code_generator.parser._finality import KnownWrite
    from datamodel_code_generator.parser._pass_decls import PassDecl

NO_WRITE_KINDS = frozenset({PassKind.SETUP, PassKind.CHECK, PassKind.BOOKKEEPING})
CHECK_WRITES = frozenset({RenderFact.STDERR})
"""What a check pass may write: the warnings it emits."""

WITNESS_RUNNER = """\
import json, os, shlex, sys
from datamodel_code_generator.__main__ import main
for name in json.loads(sys.argv[3]):
    os.environ[name] = "0"
for witness, output, report in json.loads(sys.argv[1]):
    os.environ[sys.argv[2]] = f"report:{report}"
    if main([*shlex.split(witness), "--output", output]):
        raise SystemExit(f"witness {witness!r} fails")
"""
"""Runs every witness in one interpreter, each with its own output and report file."""


def declaration_problems(declarations: Iterable[PassDecl] = PASS_DECLS) -> list[str]:
    """Return what is wrong with the pass declarations, in execution order."""
    problems: list[str] = []
    seen: set[str] = set()
    previous = None
    for decl in declarations:
        if decl.name in seen:
            problems.append(f"{decl.name} is declared twice")
        seen.add(decl.name)
        if previous is not None and decl.phase.value < previous.phase.value:
            problems.append(f"{decl.name} runs in {decl.phase.name}, before {previous.name} in {previous.phase.name}")
        previous = decl
        if decl.kind is PassKind.REPAIR and not decl.removed_by:
            problems.append(f"repair pass {decl.name} must name the change that removes it")
        if decl.kind in NO_WRITE_KINDS and decl.writes - (CHECK_WRITES if decl.kind is PassKind.CHECK else frozenset()):
            problems.append(f"{decl.kind.value} pass {decl.name} must not write facts")
    return problems


def witness_problems(known: Iterable[KnownWrite] = KNOWN_WRITES) -> list[str]:
    """Run each witness with the check reporting and return the entries whose witness no longer makes them."""
    entries = list(known)
    runs = sorted({entry.witness for entry in entries})
    with TemporaryDirectory() as directory:
        reports = [Path(directory) / f"report-{index}.jsonl" for index in range(len(runs))]
        jobs = [
            (arguments, str(Path(directory) / f"output-{index}"), str(report))
            for index, (arguments, report) in enumerate(zip(runs, reports, strict=True))
        ]
        run = subprocess.run(
            [sys.executable, "-c", WITNESS_RUNNER, json.dumps(jobs), VARIABLE, json.dumps(OTHER_AREAS)],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        if run.returncode:
            last_line = (run.stderr.strip().splitlines() or [f"exit status {run.returncode}"])[-1]
            msg = f"the witnesses could not run: {last_line}"
            raise RuntimeError(msg)
        made = {
            arguments: {
                (record["pass_name"], record["rule"], record["row"])
                for record in map(json.loads, report.read_text(encoding="utf-8").splitlines())
            }
            if report.exists()
            else set()
            for arguments, report in zip(runs, reports, strict=True)
        }
    return [
        f"stale KNOWN_WRITES entry {entry.pass_name} {entry.rule.value} {entry.row}: its witness {entry.witness!r} no "
        "longer makes it; remove the entry"
        for entry in entries
        if (entry.pass_name, entry.rule.value, entry.row) not in made[entry.witness]
    ]


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        prog="check_fact_finality.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--witnesses", action="store_true", help="also run the witness of every known violation")
    args = parser.parse_args(argv)
    problems = [*declaration_problems(), *(witness_problems() if args.witnesses else ())]
    for problem in problems:
        print(problem, file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
