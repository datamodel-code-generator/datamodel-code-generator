"""Run Avro generation against the Apache Avro C schema pass corpus."""

from __future__ import annotations

import argparse
import ast
import sys
import tempfile
import time
from contextlib import contextmanager
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from typing import TYPE_CHECKING

from datamodel_code_generator import InputFileType, generate

if TYPE_CHECKING:
    from collections.abc import Generator
    from types import ModuleType


def iter_schema_paths(schema_pass_root: Path) -> list[Path]:
    """Return Apache Avro C schema pass files in deterministic order."""
    return sorted(path for path in schema_pass_root.iterdir() if path.is_file())


@contextmanager
def generated_module_path(module_code: str, module_name: str) -> Generator[Path]:
    """Write generated code to a temporary importable module."""
    with tempfile.TemporaryDirectory(prefix="dcg-apache-avro-") as temporary_directory:
        module_path = Path(temporary_directory) / f"{module_name}.py"
        module_path.write_text(module_code, encoding="utf-8")
        yield module_path


def remove_imported_module(module_name: str) -> None:
    """Remove a generated module from sys.modules after import validation."""
    sys.modules.pop(module_name, None)


def import_module_from_path(module_name: str, module_path: Path) -> ModuleType:
    """Import a generated single-file module from a concrete path."""
    spec = spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        msg = f"Could not create import spec for {module_path}"
        raise ImportError(msg)
    module = module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "schema_pass_root",
        type=Path,
        help="Path to apache/avro lang/c/tests/schema_tests/pass",
    )
    parser.add_argument(
        "--expected-schemas",
        type=int,
        help="Expected number of schema pass files in the pinned Apache Avro corpus.",
    )
    parser.add_argument(
        "--progress-interval",
        type=int,
        default=10,
        help="Print progress after this many schemas. Use 0 to disable.",
    )
    parser.add_argument(
        "--max-failures",
        type=int,
        default=20,
        help="Maximum failures to print before returning a non-zero exit status.",
    )
    return parser.parse_args()


def main() -> int:
    """Run the Apache Avro schema pass e2e check."""
    args = parse_args()
    schema_pass_root = args.schema_pass_root.resolve()
    if not schema_pass_root.is_dir():
        print(f"Apache Avro schema pass directory does not exist: {schema_pass_root}", file=sys.stderr)
        return 2

    schema_paths = iter_schema_paths(schema_pass_root)
    if args.expected_schemas is not None and len(schema_paths) != args.expected_schemas:
        print(
            f"Expected {args.expected_schemas} Apache Avro schema pass files, found {len(schema_paths)}.",
            file=sys.stderr,
        )
        return 1

    failures: list[str] = []
    started_at = time.monotonic()
    for index, schema_path in enumerate(schema_paths, start=1):
        relative_schema_path = schema_path.relative_to(schema_pass_root)
        try:
            generated = generate(
                schema_path,
                input_file_type=InputFileType.Avro,
                disable_timestamp=True,
                formatters=[],
            )
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{relative_schema_path}: generate failed with {type(exc).__name__}: {exc}")
            continue

        if not isinstance(generated, str) or not generated.strip():
            failures.append(f"{relative_schema_path}: generate returned no module code")
            continue

        try:
            ast.parse(generated)
        except SyntaxError as exc:
            failures.append(f"{relative_schema_path}: invalid Python syntax at {exc.lineno}:{exc.offset}: {exc.msg}")
            continue

        module_name = f"generated_avro_schema_{index}"
        try:
            with generated_module_path(generated, module_name) as module_path:
                import_module_from_path(module_name, module_path)
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{relative_schema_path}: generated module failed to import: {type(exc).__name__}: {exc}")
        finally:
            remove_imported_module(module_name)

        if args.progress_interval and index % args.progress_interval == 0:
            elapsed = time.monotonic() - started_at
            print(
                f"checked {index}/{len(schema_paths)} Apache Avro schema pass files "
                f"({len(failures)} failures, {elapsed:.1f}s)"
            )

    elapsed = time.monotonic() - started_at
    print(f"checked {len(schema_paths)} Apache Avro schema pass files in {elapsed:.1f}s")
    if failures:
        print(f"{len(failures)} Apache Avro schema pass e2e failures:", file=sys.stderr)
        for failure in failures[: args.max_failures]:
            print(f"  {failure}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
