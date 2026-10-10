"""Run mypy, Pyright, and ty in strict modes over a directory, and report their errors by file, line, and rule."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Final

PYTHON_VERSION = "3.11"
MYPY: Final = ("uvx", "--quiet", "mypy@2.4.0")
PYRIGHT: Final = ("uvx", "--quiet", "pyright@1.1.414")
TIMEOUT: Final = 600
BIN = Path(sys.executable).parent
_MYPY = re.compile(r"^(\S+?):(\d+): error: .*\[([\w-]+)\]$", re.MULTILINE)
_TY = re.compile(r"^(\S+?):(\d+):\d+: error\[([\w-]+)\]", re.MULTILINE)

Found = list[tuple[str, int, str]]


def _run(command: list[str | Path], root: Path) -> subprocess.CompletedProcess[str]:
    """Run one checker, refusing a stall, a crash, or an exit status that no diagnostics explain."""
    completed = subprocess.run(command, cwd=root, capture_output=True, text=True, check=False, timeout=TIMEOUT)
    if completed.returncode not in {0, 1}:
        msg = f"{command[0]} exited with {completed.returncode}:\n{completed.stdout}{completed.stderr}"
        raise RuntimeError(msg)
    return completed


def _diagnostics(completed: subprocess.CompletedProcess[str], found: Found) -> Found:
    if completed.returncode != (1 if found else 0):
        msg = f"The checker exited with {completed.returncode} for {len(found)} errors:\n{completed.stdout}{completed.stderr}"
        raise RuntimeError(msg)
    return found


def _mypy(root: Path, targets: list[str]) -> Found:
    completed = _run(
        [
            *MYPY,
            "--strict",
            "--follow-imports=silent",
            "--no-incremental",
            "--python-executable",
            sys.executable,
            "--python-version",
            PYTHON_VERSION,
            *targets,
        ],
        root,
    )
    return _diagnostics(completed, [(match[1], int(match[2]), match[3]) for match in _MYPY.finditer(completed.stdout)])


def _pyright(root: Path, targets: list[str]) -> Found:
    (root / "pyrightconfig.json").write_text(
        json.dumps({
            "typeCheckingMode": "strict",
            "pythonVersion": PYTHON_VERSION,
            "include": targets,
            "extraPaths": ["."],
        }),
        encoding="utf-8",
    )
    completed = _run([*PYRIGHT, "--outputjson", "--pythonpath", sys.executable, "--project", "pyrightconfig.json"], root)
    return _diagnostics(
        completed,
        [
            (Path(item["file"]).relative_to(root.resolve()).as_posix(), item["range"]["start"]["line"] + 1, item["rule"])
            for item in json.loads(completed.stdout)["generalDiagnostics"]
            if item["severity"] == "error"
        ],
    )


def _ty(root: Path, targets: list[str]) -> Found:
    completed = _run(
        [
            BIN / "ty",
            "check",
            "--python",
            sys.executable,
            "--python-version",
            PYTHON_VERSION,
            "--output-format",
            "concise",
            "--extra-search-path",
            ".",
            *targets,
        ],
        root,
    )
    return _diagnostics(completed, [(match[1], int(match[2]), match[3]) for match in _TY.finditer(completed.stdout)])


CHECKERS = (("mypy", _mypy), ("pyright", _pyright), ("ty", _ty))


def checked(root: Path, targets: list[str], label: str) -> list[str]:
    """Report each checker's errors over the targets, or that it found none."""
    lines: list[str] = []
    for name, check in CHECKERS:
        found = check(root, targets)
        lines.append(f"{name} {label}: {'clean' if not found else ''}".rstrip())
        lines.extend(f"  {file}:{line} {rule}" for file, line, rule in sorted(found))
    return lines


def marked_lines(path: Path) -> set[int]:
    """Return the lines of a negative sample that end with ``# error``, each of which must hold one error."""
    return {index for index, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1) if line.endswith("# error")}


def negative(root: Path, sample: str, marked: set[int]) -> list[str]:
    """Report, for each checker, how many marked lines of a sample hold exactly one error, and every error it found."""
    lines: list[str] = []
    for name, check in CHECKERS:
        found = check(root, [sample])
        counts = Counter(line for file, line, _ in found if file == sample)
        lines.append(
            f"{name} {sample}: {sum(counts[line] == 1 for line in marked)}/{len(marked)} marked lines"
            f" with one error, {sum(count for line, count in counts.items() if line not in marked)} elsewhere,"
            f" {len([item for item in found if item[0] != sample])} in other files"
        )
        lines.extend(f"  {line} {rule}" for file, line, rule in sorted(found) if file == sample)
    return lines
