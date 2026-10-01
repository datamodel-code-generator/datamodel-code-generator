"""Type-check the model codec samples against the runtime and a package generated from the adapters fixture."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from tests.data.python.model_codec_adapters import adapter_codec_report
from tests.data.python.strict_typing import checked, marked_lines, negative

if TYPE_CHECKING:
    import pytest

CODECS = Path(__file__).parents[1] / "generation_platform" / "codecs"
SAMPLES = CODECS / "typing"
ADAPTERS = CODECS / "adapters"


def codec_typing_report(root: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """Check the positive samples clean, then each negative sample's marked lines, against the generated package."""
    adapter_codec_report(ADAPTERS / "adapters.yaml", ADAPTERS / "adapters.json", root, monkeypatch)
    root /= "accepted"
    (package := root / "samples").mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    samples = sorted(f"samples/{path.name}" for path in SAMPLES.glob("*.py"))
    for sample in samples:
        shutil.copyfile(SAMPLES / Path(sample).name, root / sample)
    lines = checked(root, [sample for sample in samples if "negative" not in sample], "positive samples")
    for sample in (sample for sample in samples if "negative" in sample):
        marked = marked_lines(root / sample)
        lines.extend((f"{sample}: {len(marked)} marked lines", *negative(root, sample, marked)))
    return "\n".join(lines) + "\n"
