"""Measure standalone built-in renderers and their output-buffer strategies.

This is a development aid, not a CI performance assertion. Run it from the
repository root with ``uv run --no-sync python scripts/benchmark_builtin_templates.py``.
"""

from __future__ import annotations

import argparse
import statistics
import sys
from pathlib import Path
from timeit import repeat
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable


ROOT = Path(__file__).resolve().parents[1]
for import_path in (ROOT, ROOT / "src"):
    if str(import_path) not in sys.path:
        sys.path.insert(0, str(import_path))


def _micro_output_strategies() -> dict[str, Callable[[], str]]:
    class Value:
        name = "field"
        type_hint = "str"

    value = Value()
    parts: list[str] = []

    def fine_append() -> str:
        parts.clear()
        parts.append("    ")
        parts.append(str(value.name))
        parts.append(": ")
        parts.append(str(value.type_hint))
        parts.append("\n")
        return "".join(parts)

    def fused_fstring() -> str:
        parts.clear()
        name = str(value.name)
        type_hint = str(value.type_hint)
        parts.append(f"    {name}: {type_hint}\n")
        return "".join(parts)

    def direct_concat() -> str:
        parts.clear()
        parts.append("    " + str(value.name) + ": " + str(value.type_hint) + "\n")
        return "".join(parts)

    return {"fine append": fine_append, "fused f-string": fused_fstring, "direct concat": direct_concat}


def _renderers() -> dict[str, Callable[[], str]]:
    from datamodel_code_generator.model._compiled_templates import get_builtin_renderer  # noqa: PLC0415, PLC2701
    from datamodel_code_generator.model.base import get_template  # noqa: PLC0415

    context = {"class_name": "Bench", "description": None, "py_type": "str"}
    compiled = get_builtin_renderer("ScalarTypeAliasAnnotation.jinja2")
    if compiled is None:  # pragma: no cover - checks committed generated registry
        error_message = "compiled ScalarTypeAliasAnnotation renderer is missing"
        raise RuntimeError(error_message)
    jinja = get_template(Path("ScalarTypeAliasAnnotation.jinja2"))
    return {"standalone renderer": lambda: compiled(**context), "Jinja renderer": lambda: jinja.render(**context)}


def _measure(function: Callable[[], str], *, number: int, repeats: int) -> float:
    """Return a robust median per-call duration in microseconds."""
    samples = repeat(function, number=number, repeat=repeats)
    return statistics.median(samples) * 1_000_000 / number


def _positive_int(value: str) -> int:
    """Parse an integer that can produce at least one benchmark sample."""
    if (parsed := int(value)) > 0:
        return parsed
    error_message = "must be a positive integer"
    raise argparse.ArgumentTypeError(error_message)


def main(argv: list[str] | None = None) -> int:
    """Run string-building and isolated renderer measurements."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--number", type=_positive_int, default=100_000, help="calls per sample")
    parser.add_argument("--repeats", type=_positive_int, default=7, help="number of samples")
    args = parser.parse_args(argv)
    for label, function in {**_micro_output_strategies(), **_renderers()}.items():
        print(f"{label}: {_measure(function, number=args.number, repeats=args.repeats):.3f} us/op")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
