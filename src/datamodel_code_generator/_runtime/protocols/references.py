"""Operation references shared by generated helpers and their errors."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True, kw_only=True)
class OperationRef:
    """Identify an operation by its JSON pointer and optional root document."""

    pointer: str
    document: str | None = None
