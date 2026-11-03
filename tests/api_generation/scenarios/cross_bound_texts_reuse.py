"""Call the bound texts server through its client when reuse makes equal parameter objects share one model.

Each operation's parameters and forms are written as without reuse: the members and kinds of the model each use
binds.
"""

from __future__ import annotations

from tests.api_generation.scenarios.cross_bound_texts import DOCUMENT, calls, services

MODEL = {"reuse_model": True}

__all__ = ["DOCUMENT", "MODEL", "calls", "services"]
