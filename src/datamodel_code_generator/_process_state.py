"""Serialize operations that temporarily modify process-wide state."""

from __future__ import annotations

from threading import RLock

PROCESS_STATE_LOCK = RLock()
