"""The scope of one parse: the modes its checks run with, read when its pipeline starts."""

from __future__ import annotations

from functools import wraps
from typing import TYPE_CHECKING, Any

from tests.checks.areas import MODES, read_modes

if TYPE_CHECKING:
    from collections.abc import Callable


def run_steps(original: Callable[..., None]) -> Callable[..., None]:
    """Wrap ``Runner.run_steps``: the run of the whole pipeline (``PIPELINE``) of a parse reads the modes of its checks.

    ``Parser.parse`` itself stays unwrapped: it remembers the frame that called it, for the warnings that name it.
    """

    @wraps(original)
    def run_steps(self: Any, steps: object, parser: object, state: object) -> None:
        from datamodel_code_generator.parser._passes._tables import PIPELINE

        if steps is not PIPELINE:
            return original(self, steps, parser, state)
        token = MODES.set(read_modes())
        try:
            return original(self, steps, parser, state)
        finally:
            MODES.reset(token)

    return run_steps
