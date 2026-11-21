"""Review R18, must M3: a lambda inside a deferred class annotation is a scope that is never compiled."""

from __future__ import annotations

from typing import Annotated


class M:
    x: Annotated[list[int], (lambda: [])] = []
