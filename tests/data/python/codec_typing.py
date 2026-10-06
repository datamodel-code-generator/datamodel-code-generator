"""Type-check the model codec samples against the runtime."""

from __future__ import annotations

import shutil
from pathlib import Path

from tests.data.python.strict_typing import checked, marked_lines, negative

CODECS = Path(__file__).parents[1] / "generation_platform" / "codecs"
SAMPLES = CODECS / "typing"


def codec_typing_report(root: Path) -> str:
    """Check the positive samples clean, then each negative sample's marked lines."""
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
