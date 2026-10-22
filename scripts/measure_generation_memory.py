"""Measure peak resident memory while generating models from a JSON Schema."""

from __future__ import annotations

import argparse
import importlib
import json
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING

from datamodel_code_generator import DataModelType, InputFileType, generate

if TYPE_CHECKING:
    from collections.abc import Sequence

resource = None if sys.platform == "win32" else importlib.import_module("resource")


def reset_peak_rss() -> bool:
    """Reset this process's resident-set high-water mark where the kernel allows it (Linux with ``clear_refs``).

    Return whether it was reset: a forked child otherwise inherits its parent's high-water mark on Linux.
    """
    clear_refs = Path("/proc/self/clear_refs")
    if sys.platform != "linux" or not clear_refs.exists():
        return False
    try:
        clear_refs.write_text("5", encoding="ascii")
    except OSError:
        return False
    return True


def read_peak_rss_bytes(*, reset: bool) -> int:
    """Return this process's peak resident memory in bytes (``ru_maxrss`` is bytes on macOS, KiB elsewhere)."""
    if reset:
        status = Path("/proc/self/status").read_text(encoding="ascii")
        return int(next(line for line in status.splitlines() if line.startswith("VmHWM:")).split()[1]) * 1024
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak if sys.platform == "darwin" else peak * 1024


def main(argv: Sequence[str] | None = None) -> int:
    """Run one no-formatter generation measurement in the current process."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="JSON Schema input path")
    parser.add_argument("output", type=Path, help="generated Python output path")
    parser.add_argument(
        "--output-model-type",
        choices=(
            DataModelType.PydanticV2BaseModel.value,
            DataModelType.DataclassesDataclass.value,
            DataModelType.TypingTypedDict.value,
            DataModelType.MsgspecStruct.value,
        ),
        default=DataModelType.PydanticV2BaseModel.value,
    )
    args = parser.parse_args(argv)
    if resource is None:  # pragma: no cover
        parser.error("peak RSS measurement is supported only on Unix")

    reset = reset_peak_rss()
    started_at = time.perf_counter()
    generate(
        input_=args.input,
        input_file_type=InputFileType.JsonSchema,
        output=args.output,
        output_model_type=DataModelType(args.output_model_type),
        formatters=[],
    )
    elapsed_seconds = time.perf_counter() - started_at
    peak_rss_bytes = read_peak_rss_bytes(reset=reset)
    print(
        json.dumps(
            {
                "elapsed_seconds": elapsed_seconds,
                "input_bytes": args.input.stat().st_size,
                "output_bytes": args.output.stat().st_size,
                "output_model_type": args.output_model_type,
                "peak_rss_bytes": peak_rss_bytes,
                "platform": sys.platform,
            },
            sort_keys=True,
        ),
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
