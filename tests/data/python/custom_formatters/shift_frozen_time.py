"""Test formatter that moves a frozen clock on while a generation runs."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from datamodel_code_generator.format import CustomCodeFormatter

if TYPE_CHECKING:
    import time_machine


class CodeFormatter(CustomCodeFormatter):
    """Leave generated code unchanged, moving the frozen clock a test hands over on by a second per file."""

    clock: ClassVar[time_machine.Coordinates]

    def apply(self, code: str) -> str:
        """Shift the clock once the generation has taken its timestamp."""
        type(self).clock.shift(1)
        return code
