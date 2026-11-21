"""Check the outputs a test run exported for an older target on that target's own Python.

Usage:
    DATAMODEL_CODE_GENERATOR_EXPORT_OLDER_TARGET_OUTPUTS=<dir> pytest ...
    python -I scripts/import_exported_outputs.py <dir>

The test run copies every output whose target Python is older than its own (``_export_older_target_output`` in
tests/main/conftest.py). This script runs on the target's Python with only the generated code's dependencies
installed, not datamodel-code-generator: it compiles each output for a target up to its own version and, with the
test suite's own import code, imports the ones a test run on that Python imports whose target Pydantic version the
installed Pydantic reaches. The repository root is importable for the modules tests point imports at. It prints a
line per failure with the test that wrote the output and exits 1 when one fails or none was compiled.
"""

from __future__ import annotations

import json
import sys
import warnings
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def release(text: str) -> tuple[int, ...]:
    """Return the major and minor release numbers of a version string."""
    return tuple(int(part) for part in text.split(".")[:2])


def main(export_root: Path) -> int:
    """Compile and import the exported outputs, and report the ones that fail."""
    import_code = compile((export_root / "import_code.py").read_text(encoding="utf-8"), "import_code.py", "exec")
    pydantic = release(installed_version("pydantic"))
    checked = imported = failed = 0
    for case_file in sorted(export_root.glob("*/case.json")):
        case = json.loads(case_file.read_text(encoding="utf-8"))
        if tuple(case["target"]) > sys.version_info[:2]:
            continue
        output_path = case_file.parent / case["output"]
        checked += 1
        try:
            for python_file in [output_path] if output_path.is_file() else sorted(output_path.rglob("*.py")):
                compile(python_file.read_text(encoding="utf-8"), str(python_file), "exec", dont_inherit=True)
            if case["imports"] and release(case["pydantic"]) <= pydantic:
                imported += 1
                exec(import_code, {"output_path": output_path})
        except Exception as error:  # noqa: BLE001
            failed += 1
            print(f"FAILED {case['node']} ({case_file.parent.name}): {type(error).__name__}: {error}")
    dependencies = ", ".join(f"{name} {installed_version(name)}" for name in ("pydantic", "msgspec"))
    print(
        f"{checked} outputs compiled, {imported} imported, {failed} failed "
        f"on Python {sys.version.split()[0]} ({dependencies})"
    )
    return 1 if failed or not checked else 0


def installed_version(distribution: str) -> str:
    """Return the installed version of a distribution, or ``0`` when it is missing."""
    try:
        return version(distribution)
    except PackageNotFoundError:
        return "0"


if __name__ == "__main__":
    sys.path.append(str(ROOT))
    warnings.simplefilter("ignore")
    raise SystemExit(main(Path(sys.argv[1])))
