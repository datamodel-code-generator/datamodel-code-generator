"""Check that the known finality violations still happen, so that ``KNOWN_WRITES`` shrinks.

``KNOWN_WRITES`` in ``tests/checks/finality.py`` lists the violations of today's pipeline that
``DATAMODEL_CODE_GENERATOR_CHECK_FACT_FINALITY`` reports but never raises. Each entry names a
witness, the arguments of one ``datamodel-codegen`` run that makes it. The script runs every witness with the check
reporting and lists the entries whose witness no longer makes their violation: the change that removed it removes the
entry too. The pass declarations (``scripts/pass_decls.py``) refuse their own contradictions when they are built, so
importing this script checks them.

Usage:
    python scripts/check_fact_finality.py
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
OTHER_AREAS = ("DATAMODEL_CODE_GENERATOR_CHECK_SCHEMA_LOCATIONS",)

for path in (SRC, ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from tests.checks.finality import KNOWN_WRITES  # noqa: E402

if TYPE_CHECKING:
    from collections.abc import Iterable

    from tests.checks.finality import KnownWrite

WITNESS_RUNNER = """\
import json, os, shlex, sys
from tests.checks.__main__ import main
for name in json.loads(sys.argv[3]):
    os.environ[name] = "0"
for witness, output, report in json.loads(sys.argv[1]):
    os.environ[sys.argv[2]] = f"report:{report}"
    if main([*shlex.split(witness), "--output", output]):
        raise SystemExit(f"witness {witness!r} fails")
"""
"""Runs every witness in one interpreter, each with its own output and report file."""


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
    parser.parse_args(argv)
    problems = witness_problems()
    for problem in problems:
        print(problem, file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
