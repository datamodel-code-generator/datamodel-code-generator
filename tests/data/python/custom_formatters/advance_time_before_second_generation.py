"""Test formatter that waits for a real clock boundary between two generation runs."""

from __future__ import annotations

from datetime import datetime, timezone
from time import sleep

from datamodel_code_generator.format import CustomCodeFormatter


class CodeFormatter(CustomCodeFormatter):
    """Leave generated code unchanged while advancing the real clock for the second run."""

    apply_count = 0
    crossed_second_boundary = False

    def apply(self, code: str) -> str:
        """Wait until the next UTC second immediately before the second header is built."""
        type(self).apply_count += 1
        if type(self).apply_count == 2:
            now = datetime.now(timezone.utc)
            sleep((1_000_000 - now.microsecond) / 1_000_000 + 0.02)
            type(self).crossed_second_boundary = int(datetime.now(timezone.utc).timestamp()) > int(now.timestamp())
        return code
