"""Observe final ordinary module returns and release borrowed graph ownership."""

from __future__ import annotations

import gc
import json
from pathlib import Path

import pytest

from tests.conftest import assert_output

DATA = Path(__file__).parents[1] / "data"
EXPECTED = DATA / "expected/main/generation_platform/binding"


@pytest.mark.parametrize("chained", [False, True])
def test_recording_failure_does_not_retain_disposed_models(monkeypatch: pytest.MonkeyPatch, *, chained: bool) -> None:
    """Keep first-failure identity while releasing direct and chained traceback graph roots."""
    from tests.data.python.binding_failure_inputs import failed_module_capture

    parser, references, failure = failed_module_capture(
        DATA / "generation_platform/binding/empty-fixed-tuple.json", monkeypatch, chained=chained
    )
    gc.collect()
    assert_output(
        json.dumps({
            "graph_released": bool(references) and all(reference() is None for reference in references),
            "failure_preserved": failure is not None and parser.binding_ledger.failure is failure,
            "traceback_released": failure is not None and failure.__traceback__ is None,
        })
        + "\n",
        EXPECTED / "module-failure-released.txt",
    )
