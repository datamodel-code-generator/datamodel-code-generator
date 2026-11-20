"""The one place that starts another program and returns what it printed (the code formatter and ``protoc``)."""

from __future__ import annotations

import subprocess  # noqa: S404
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path
    from typing import TypeAlias

    ProcessResult: TypeAlias = subprocess.CompletedProcess[bytes]


def run_captured(
    command: tuple[str, ...], *, cwd: Path | str | None = None, stdin: bytes | None = None
) -> ProcessResult:
    """Run `command` and return its exit code, standard output and standard error as bytes; never raise on a code."""
    return subprocess.run(  # noqa: S603
        command,
        capture_output=True,
        check=False,
        cwd=cwd,
        input=stdin,
    )
