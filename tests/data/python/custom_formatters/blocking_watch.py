"""Custom formatter used to coordinate a bounded watch-mode integration test."""

from __future__ import annotations

import os
from pathlib import Path
from time import monotonic, sleep

from datamodel_code_generator.format import CustomCodeFormatter

_MARKER_ENV = "DATAMODEL_CODEGEN_WATCH_MARKER"
_RELEASE_ENV = "DATAMODEL_CODEGEN_WATCH_RELEASE"
_TIMEOUT_ENV = "DATAMODEL_CODEGEN_WATCH_BLOCK_TIMEOUT"


class CodeFormatter(CustomCodeFormatter):
    """Wait once so an E2E test can write an event during regeneration."""

    _has_blocked = False

    def apply(self, code: str) -> str:
        """Return unchanged generated code after the test releases the formatter."""
        if not type(self)._has_blocked:
            marker_file = Path(os.environ[_MARKER_ENV])
            release_file = Path(os.environ[_RELEASE_ENV])
            deadline = monotonic() + float(os.environ.get(_TIMEOUT_ENV, "5"))
            marker_file.touch()
            while not release_file.is_file():
                if monotonic() >= deadline:
                    msg = "Timed out waiting for the watch test to release the custom formatter"
                    raise RuntimeError(msg)
                sleep(0.01)
            type(self)._has_blocked = True
        return code
