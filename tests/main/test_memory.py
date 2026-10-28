"""Peak resident memory regression guards for large-schema generation."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, TypedDict, cast

import pytest

from datamodel_code_generator import DataModelType
from scripts.measure_generation_memory import main as measure_generation_memory

if TYPE_CHECKING:
    from _pytest.tmpdir import TempPathFactory

CATASTROPHIC_PEAK_RSS_LIMIT_BYTES = 768 * 1024 * 1024
MINIMUM_LARGE_SCHEMA_BYTES = 700 * 1024
REPO_ROOT = Path(__file__).resolve().parents[2]
MEMORY_MODEL_TYPES = (
    pytest.param(DataModelType.PydanticV2BaseModel.value, id="pydantic-v2"),
    pytest.param(DataModelType.DataclassesDataclass.value, id="dataclass"),
    pytest.param(DataModelType.TypingTypedDict.value, id="typed-dict"),
    pytest.param(DataModelType.MsgspecStruct.value, id="msgspec"),
)


class MemoryMetrics(TypedDict):
    """Machine-readable output from the generation memory probe."""

    elapsed_seconds: float
    input_bytes: int
    output_bytes: int
    output_model_type: str
    peak_rss_bytes: int
    platform: str


def _append_step_summary(report_line: str, summary_path: str | None) -> None:
    if summary_path is None:
        return
    with Path(summary_path).open("a", encoding="utf-8") as summary:
        summary.write(f"- {report_line}\n")


@pytest.mark.allow_direct_assert
@pytest.mark.skipif(sys.platform == "win32", reason="resource.getrusage is unavailable on Windows")
def test_measure_generation_memory(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Run the measurement entry point and emit complete machine-readable metrics."""
    input_path = tmp_path / "schema.json"
    input_path.write_text(
        json.dumps(
            {
                "title": "Small",
                "type": "object",
                "properties": {"value": {"type": "string"}},
            },
        ),
        encoding="utf-8",
    )
    output_path = tmp_path / "output.py"

    assert measure_generation_memory([str(input_path), str(output_path)]) == 0
    metrics = cast("MemoryMetrics", json.loads(capsys.readouterr().out))
    assert metrics["elapsed_seconds"] > 0
    assert metrics["input_bytes"] == input_path.stat().st_size
    assert metrics["output_bytes"] == output_path.stat().st_size
    assert metrics["output_model_type"] == DataModelType.PydanticV2BaseModel.value
    assert metrics["peak_rss_bytes"] > 0
    assert metrics["platform"] == sys.platform


@pytest.mark.allow_direct_assert
def test_append_step_summary(tmp_path: Path) -> None:
    """Write workflow summaries only when GitHub provides a destination."""
    summary_path = tmp_path / "summary.md"
    _append_step_summary("not written", None)
    assert not summary_path.exists()

    _append_step_summary("peak_rss=123.4 MiB", str(summary_path))
    assert summary_path.read_text(encoding="utf-8") == "- peak_rss=123.4 MiB\n"


@pytest.mark.perf
@pytest.mark.allow_direct_assert
@pytest.mark.skipif(sys.platform == "win32", reason="resource.getrusage is unavailable on Windows")
@pytest.mark.parametrize("output_model_type", MEMORY_MODEL_TYPES)
def test_perf_extreme_large_schema_peak_rss(
    tmp_path_factory: TempPathFactory,
    extreme_large_schema: Path,
    output_model_type: str,
) -> None:
    """Keep no-formatter generation of 2000 models below a catastrophic RSS ceiling."""
    output_path = tmp_path_factory.mktemp("memory-output") / "output.py"
    result = subprocess.run(
        [
            sys.executable,
            "scripts/measure_generation_memory.py",
            str(extreme_large_schema),
            str(output_path),
            "--output-model-type",
            output_model_type,
        ],
        check=True,
        capture_output=True,
        cwd=REPO_ROOT,
        text=True,
        timeout=120,
    )
    metrics = cast("MemoryMetrics", json.loads(result.stdout))
    peak_rss_mib = metrics["peak_rss_bytes"] / (1024 * 1024)

    assert metrics["input_bytes"] >= MINIMUM_LARGE_SCHEMA_BYTES
    assert metrics["output_bytes"] == output_path.stat().st_size
    assert metrics["output_model_type"] == output_model_type
    assert metrics["platform"] == sys.platform
    assert metrics["elapsed_seconds"] > 0
    assert output_path.read_text(encoding="utf-8").count("class Model") >= 2000
    assert metrics["peak_rss_bytes"] < CATASTROPHIC_PEAK_RSS_LIMIT_BYTES, (
        f"{output_model_type} generation peaked at {peak_rss_mib:.1f} MiB; "
        f"catastrophic guard is {CATASTROPHIC_PEAK_RSS_LIMIT_BYTES / (1024 * 1024):.0f} MiB"
    )

    report_line = (
        f"{output_model_type}: elapsed={metrics['elapsed_seconds']:.3f}s, "
        f"peak_rss={peak_rss_mib:.1f} MiB, "
        f"input={metrics['input_bytes'] / (1024 * 1024):.1f} MiB, "
        f"output={metrics['output_bytes'] / (1024 * 1024):.1f} MiB"
    )
    sys.stdout.write(f"{report_line}\n")
    _append_step_summary(report_line, os.environ.get("GITHUB_STEP_SUMMARY"))
