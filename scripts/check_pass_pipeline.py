"""Record which passes of ``Parser.parse()`` run, in order, for one generation.

A trace has one line per pass that runs, named as in ``src/datamodel_code_generator/parser/_pass_decls.py``, and a
``-- module`` line where the pipeline starts processing each output module. The test suite compares the traces of a few
inputs with stored ones, so that a change to the pass order shows in review, and checks that some input runs every
declared pass.

Usage:
    python scripts/check_pass_pipeline.py --trace "<datamodel-codegen arguments>"
"""

from __future__ import annotations

import argparse
import shlex
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from datamodel_code_generator.__main__ import main as generate_main  # noqa: E402
from datamodel_code_generator.parser._finality import MODULE_TRACE_MARK, recording_pass_trace  # noqa: E402, PLC2701
from datamodel_code_generator.parser._pass_decls import PASS_DECLS  # noqa: E402, PLC2701

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

TEMPORARY_DIRECTORY = "{tmp}"
"""Replaced, in traced arguments, by a temporary directory that the run may write files into."""


def call_trace(call: Callable[[], object]) -> list[str]:
    """Return the pass trace of ``call``, which runs one or more generations."""
    with recording_pass_trace() as trace:
        call()
    return trace


def pass_trace(arguments: str) -> str:
    """Return the pass trace of one ``datamodel-codegen`` run with ``arguments``, and its exit status."""
    with TemporaryDirectory() as directory:
        argv = [*shlex.split(arguments.replace(TEMPORARY_DIRECTORY, directory)), "--output", f"{directory}/output"]
        status: list[int] = []
        trace = call_trace(lambda: status.append(int(generate_main(argv))))
    return "".join(f"{line}\n" for line in [*trace, f"exit status: {status[0]}"])


def unrun_passes(traces: Iterable[str]) -> list[str]:
    """Return the declared passes that none of ``traces`` runs, in declaration order."""
    run = {line for trace in traces for line in trace.splitlines() if line != MODULE_TRACE_MARK}
    return [decl.name for decl in PASS_DECLS if decl.name not in run]


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        prog="check_pass_pipeline.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--trace", metavar="ARGUMENTS", required=True, help="datamodel-codegen arguments to trace")
    args = parser.parse_args(argv)
    sys.stdout.write(pass_trace(args.trace))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
