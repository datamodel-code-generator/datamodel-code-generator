"""The single HTTP omission marker owned by one generated package runtime."""

from __future__ import annotations

from enum import Enum
from typing import Final, Literal


class Unset(Enum):
    """Mark an omitted HTTP value, distinct from JSON null and native model sentinels."""

    UNSET = "UNSET"

    def __repr__(self) -> str:
        """Render the marker by its public name."""
        return "UNSET"

    def __bool__(self) -> Literal[False]:
        """Treat omission as falsy, like an absent value."""
        return False


UNSET: Final = Unset.UNSET
