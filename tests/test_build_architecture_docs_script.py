"""Tests for architecture documentation synchronization."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from scripts import build_architecture_docs
from tests.conftest import assert_output

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build_architecture_docs.py"
EXPECTED_ARCHITECTURE_DOCS_PATH = ROOT / "tests" / "data" / "expected" / "architecture_docs"
CORE_INVENTORY_MARKERS = (
    "OpenAPIParser",
    "JsonSchemaParser",
    "`pydantic_v2.BaseModel`",
    "`black`",
)


def _completed_process_output(result: subprocess.CompletedProcess[str]) -> str:
    """Format a subprocess result for stable external-file comparison."""
    return f"returncode: {result.returncode}\nstdout:\n{result.stdout}stderr:\n{result.stderr}"


def _core_inventory_marker_output(content: str) -> str:
    """Extract representative generated inventory markers for external-file comparison."""
    return "".join(f"{marker}\n" for marker in CORE_INVENTORY_MARKERS if marker in content)


def test_build_architecture_docs_check_is_up_to_date() -> None:
    """Check that committed architecture docs match generated source inventory."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert_output(
        _completed_process_output(result),
        EXPECTED_ARCHITECTURE_DOCS_PATH / "build_architecture_docs_check.txt",
    )


def test_architecture_inventory_contains_core_routes() -> None:
    """Check that the generated inventory includes representative source-derived entries."""
    content = build_architecture_docs.generate_architecture_inventory()

    assert_output(
        _core_inventory_marker_output(content),
        EXPECTED_ARCHITECTURE_DOCS_PATH / "architecture_inventory_core.txt",
    )
