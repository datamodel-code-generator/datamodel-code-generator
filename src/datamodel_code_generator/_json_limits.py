"""Nesting limits of JSON settings, shared by option loading and the targets that validate the values."""

from __future__ import annotations

from typing import Final

CLIENT_PROTOCOLS_MAX_DEPTH: Final = 64

__all__ = ["CLIENT_PROTOCOLS_MAX_DEPTH"]
