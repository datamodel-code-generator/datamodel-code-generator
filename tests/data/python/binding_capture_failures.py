"""Reject inconsistent producer records through the real generation session."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from tests.data.python.binding_provenance_failures import producer_fault
from tests.data.python.generation_session_inputs import run_generation_session

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def producer_failure(source: Path, case: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    """Exercise driver rejection, accepted-batch absence, and complete failed-attempt release."""
    with producer_fault(case, monkeypatch):
        document = {
            **json.loads(source.read_text(encoding="utf-8")),
            "$schema": "https://json-schema.org/draft/2020-12/schema",
        }
        actual, retained = run_generation_session(source, document=document)
    return {
        "error": actual["error"],
        "batch": actual["batch"],
        "retained_parsers": actual["retained_parsers"],
        "retained_graph": retained,
    }
