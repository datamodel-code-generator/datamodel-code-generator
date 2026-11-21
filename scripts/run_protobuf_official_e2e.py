"""Run Protocol Buffers generation against the official protobuf .proto corpus."""

from __future__ import annotations

import argparse
import ast
import importlib.util
import os
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from datamodel_code_generator import InputFileType, generate

if TYPE_CHECKING:
    from collections.abc import Generator
    from types import ModuleType


SKIP_NAME_PARTS = ("bad_identifiers", "invalid", "unstable")
SKIP_FILE_NAMES = {"unittest_empty.proto"}
SKIP_TEXT_MARKERS = (
    'edition = "UNSTABLE"',
    'edition = "2024"',
    'edition = "999',
)


def iter_proto_paths(corpus_root: Path) -> list[Path]:
    """Return official protobuf corpus files that should compile with bundled protoc."""
    roots = [corpus_root / "src", corpus_root / "conformance", corpus_root / "benchmarks"]
    paths: list[Path] = []
    for root in roots:
        if root.is_dir():
            paths.extend(path for path in root.rglob("*.proto") if path.is_file())
    return [path for path in sorted(paths) if not should_skip(path)]


def should_skip(path: Path) -> bool:
    """Skip upstream parser-negative fixtures and schemas unsupported by bundled protoc."""
    if path.name in SKIP_FILE_NAMES or any(part in path.name for part in SKIP_NAME_PARTS):
        return True
    text = path.read_text(encoding="utf-8", errors="ignore")
    return any(marker in text for marker in SKIP_TEXT_MARKERS)


def corpus_working_directory(corpus_root: Path, proto_path: Path) -> Path:
    """Choose an include-root cwd that matches how the upstream proto imports are written."""
    src_root = corpus_root / "src"
    conformance_root = corpus_root / "conformance"
    if src_root in proto_path.parents:
        return src_root
    if conformance_root in proto_path.parents:
        return corpus_root
    return proto_path.parent


@contextmanager
def working_directory(path: Path) -> Generator[None, None, None]:
    """Temporarily change cwd so list[Path] generation preserves corpus-relative imports."""
    old_cwd = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(old_cwd)


def import_module_from_code(module_name: str, module_code: str) -> ModuleType:
    """Import generated single-file code as a temporary Python module."""
    with tempfile.TemporaryDirectory(prefix="dcg-protobuf-official-") as temporary_directory:
        module_path = Path(temporary_directory) / f"{module_name}.py"
        module_path.write_text(module_code, encoding="utf-8")
        spec = importlib.util.spec_from_file_location(module_name, module_path)
        if spec is None or spec.loader is None:
            msg = f"Could not create import spec for {module_path}"
            raise ImportError(msg)
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        return module


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "corpus_root",
        type=Path,
        help="Path to a checkout of https://github.com/protocolbuffers/protobuf",
    )
    parser.add_argument(
        "--expected-schemas",
        type=int,
        help="Expected number of official protobuf .proto files in the pinned corpus subset.",
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
    """Run the official protobuf corpus e2e check."""
    args = parse_args()
    corpus_root = args.corpus_root.resolve()
    if not corpus_root.is_dir():
        print(f"Protocol Buffers corpus directory does not exist: {corpus_root}", file=sys.stderr)
        return 2

    proto_paths = iter_proto_paths(corpus_root)
    if args.expected_schemas is not None and len(proto_paths) != args.expected_schemas:
        print(
            f"Expected {args.expected_schemas} official protobuf schemas, found {len(proto_paths)}.",
            file=sys.stderr,
        )
        return 1

    failures: list[str] = []
    started_at = time.monotonic()
    for index, proto_path in enumerate(proto_paths, start=1):
        relative_proto_path = proto_path.relative_to(corpus_root)
        try:
            with working_directory(corpus_working_directory(corpus_root, proto_path)):
                generated = generate(
                    cast(Any, [proto_path]),  # noqa: TC006 - keep Any visible to CodeQL and ty.
                    input_file_type=InputFileType.Protobuf,
                    disable_timestamp=True,
                    formatters=[],
                )
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{relative_proto_path}: generate failed with {type(exc).__name__}: {exc}")
            continue

        if not isinstance(generated, str) or not generated.strip():
            failures.append(f"{relative_proto_path}: generate returned no module code")
            continue

        try:
            ast.parse(generated)
        except SyntaxError as exc:
            failures.append(f"{relative_proto_path}: invalid Python syntax at {exc.lineno}:{exc.offset}: {exc.msg}")
            continue

        module_name = f"generated_protobuf_schema_{index}"
        try:
            import_module_from_code(module_name, generated)
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{relative_proto_path}: generated module failed to import: {type(exc).__name__}: {exc}")
        finally:
            sys.modules.pop(module_name, None)

        if args.progress_interval and index % args.progress_interval == 0:
            elapsed = time.monotonic() - started_at
            print(
                f"checked {index}/{len(proto_paths)} official protobuf schemas "
                f"({len(failures)} failures, {elapsed:.1f}s)"
            )

    elapsed = time.monotonic() - started_at
    print(f"checked {len(proto_paths)} official protobuf schemas in {elapsed:.1f}s")
    if failures:
        print(f"{len(failures)} official protobuf e2e failures:", file=sys.stderr)
        for failure in failures[: args.max_failures]:
            print(f"  {failure}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
