"""Run XML Schema generation against the W3C XML Schema Test Suite."""

from __future__ import annotations

import argparse
import ast
import importlib
import sys
import tempfile
import time
from collections.abc import Iterable, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from typing import TYPE_CHECKING
from xml.etree import ElementTree as ET

from datamodel_code_generator import InputFileType, generate

if TYPE_CHECKING:
    from types import ModuleType

TEST_SUITE_NAMESPACE = "http://www.w3.org/XML/2004/xml-schema-test-suite/"
XLINK_NAMESPACE = "http://www.w3.org/1999/xlink"


@dataclass(frozen=True)
class SchemaDocument:
    """A valid schemaDocument entry from a W3C testSet file."""

    test_set: Path
    schema_path: Path


def iter_valid_schema_documents(suite_root: Path) -> Iterable[SchemaDocument]:
    """Yield schemaDocument entries whose schemaTest expects a valid schema."""
    namespace = {"ts": TEST_SUITE_NAMESPACE}
    href_key = f"{{{XLINK_NAMESPACE}}}href"
    for test_set in sorted(suite_root.rglob("*.testSet")):
        tree = ET.parse(test_set)
        for schema_test in tree.findall(".//ts:schemaTest", namespace):
            expected = schema_test.find("ts:expected", namespace)
            if expected is None or expected.get("validity") != "valid":
                continue
            for schema_document in schema_test.findall("ts:schemaDocument", namespace):
                href = schema_document.get(href_key)
                if href:
                    yield SchemaDocument(
                        test_set=test_set,
                        schema_path=(test_set.parent / href).resolve(),
                    )


def iter_generated_modules(generated: object) -> Iterable[tuple[tuple[str, ...] | None, str]]:
    """Normalize datamodel-code-generator single and multi-module outputs."""
    if isinstance(generated, str):
        yield None, generated
    elif isinstance(generated, Mapping):
        for raw_module_path, module_code in generated.items():
            module_path = raw_module_path if isinstance(raw_module_path, tuple) else (str(raw_module_path),)
            yield module_path, module_code


@contextmanager
def sys_path_entry(path: str) -> Iterable[None]:
    """Temporarily prepend an import path."""
    sys.path.insert(0, path)
    try:
        yield
    finally:
        sys.path.remove(path)


def remove_imported_modules(module_prefix: str) -> None:
    """Remove generated modules from sys.modules after import validation."""
    for name in list(sys.modules):
        if name == module_prefix or name.startswith(f"{module_prefix}."):
            del sys.modules[name]


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


def write_generated_module(path: Path, module_code: str) -> None:
    """Write a generated module, creating parent packages as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(module_code, encoding="utf-8")


def import_generated_modules(
    module_prefix: str,
    generated_modules: list[tuple[tuple[str, ...] | None, str]],
) -> None:
    """Import generated single and multi-module output as executable Python models."""
    with tempfile.TemporaryDirectory(prefix="dcg-w3c-xmlschema-") as temporary_directory:
        temp_path = Path(temporary_directory)
        if len(generated_modules) == 1 and generated_modules[0][0] is None:
            module_path = temp_path / f"{module_prefix}.py"
            write_generated_module(module_path, generated_modules[0][1])
            import_module_from_path(module_prefix, module_path)
            return

        package_path = temp_path / module_prefix
        for module_path, module_code in generated_modules:
            if module_path is None:
                write_generated_module(package_path / "__init__.py", module_code)
                continue
            write_generated_module(package_path.joinpath(*module_path), module_code)

        with sys_path_entry(temporary_directory):
            importlib.import_module(module_prefix)
            for module_path, _module_code in generated_modules:
                if module_path is None:
                    continue
                module_parts = list(module_path)
                if module_parts[-1] == "__init__.py":
                    import_name = ".".join([module_prefix, *module_parts[:-1]])
                else:
                    import_name = ".".join([module_prefix, *module_parts[:-1], module_parts[-1].removesuffix(".py")])
                importlib.import_module(import_name)


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "suite_root",
        type=Path,
        help="Path to a checkout of https://github.com/w3c/xsdtests",
    )
    parser.add_argument(
        "--expected-documents",
        type=int,
        help="Expected number of valid schemaDocument entries in the pinned W3C suite.",
    )
    parser.add_argument(
        "--expected-unique-documents",
        type=int,
        help="Expected number of unique valid schemaDocument paths in the pinned W3C suite.",
    )
    parser.add_argument(
        "--progress-interval",
        type=int,
        default=500,
        help="Print progress after this many unique schemas. Use 0 to disable.",
    )
    parser.add_argument(
        "--max-failures",
        type=int,
        default=20,
        help="Maximum failures to print before returning a non-zero exit status.",
    )
    return parser.parse_args()


def main() -> int:
    """Run the W3C XML Schema generation e2e check."""
    args = parse_args()
    suite_root = args.suite_root.resolve()
    if not suite_root.is_dir():
        print(f"W3C XML Schema Test Suite directory does not exist: {suite_root}", file=sys.stderr)
        return 2

    documents = list(iter_valid_schema_documents(suite_root))
    unique_paths = sorted({document.schema_path for document in documents})
    missing_paths = [path for path in unique_paths if not path.is_file()]
    if missing_paths:
        print("W3C XML Schema Test Suite has missing schemaDocument files:", file=sys.stderr)
        for path in missing_paths[: args.max_failures]:
            print(f"  {path}", file=sys.stderr)
        return 1

    if args.expected_documents is not None and len(documents) != args.expected_documents:
        print(
            f"Expected {args.expected_documents} valid schemaDocument entries, found {len(documents)}.",
            file=sys.stderr,
        )
        return 1
    if args.expected_unique_documents is not None and len(unique_paths) != args.expected_unique_documents:
        print(
            f"Expected {args.expected_unique_documents} unique valid schemaDocument paths, found {len(unique_paths)}.",
            file=sys.stderr,
        )
        return 1

    failures: list[str] = []
    started_at = time.monotonic()
    for index, schema_path in enumerate(unique_paths, start=1):
        relative_schema_path = schema_path.relative_to(suite_root).as_posix()
        try:
            generated = generate(
                schema_path,
                input_file_type=InputFileType.XMLSchema,
                disable_timestamp=True,
                formatters=[],
            )
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{relative_schema_path}: generate failed with {type(exc).__name__}: {exc}")
            continue

        generated_modules = list(iter_generated_modules(generated))
        if not generated_modules:
            failures.append(f"{relative_schema_path}: generate returned no modules")
            continue

        for module_path, module_code in generated_modules:
            module_name = "/".join(module_path) if module_path else "<single module>"
            if not isinstance(module_code, str) or not module_code.strip():
                failures.append(f"{relative_schema_path} {module_name}: generated module is empty")
                continue
            try:
                ast.parse(module_code)
            except SyntaxError as exc:
                failures.append(
                    f"{relative_schema_path} {module_name}: invalid Python syntax "
                    f"at {exc.lineno}:{exc.offset}: {exc.msg}"
                )

        module_prefix = f"generated_schema_{index}"
        try:
            import_generated_modules(module_prefix, generated_modules)
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{relative_schema_path}: generated modules failed to import: {type(exc).__name__}: {exc}")
        finally:
            remove_imported_modules(module_prefix)

        if args.progress_interval and index % args.progress_interval == 0:
            elapsed = time.monotonic() - started_at
            print(
                f"checked {index}/{len(unique_paths)} unique valid schemaDocuments "
                f"({len(failures)} failures, {elapsed:.1f}s)"
            )

    elapsed = time.monotonic() - started_at
    print(f"checked {len(documents)} valid schemaDocument entries ({len(unique_paths)} unique paths) in {elapsed:.1f}s")
    if failures:
        print(f"{len(failures)} W3C XML Schema e2e failures:", file=sys.stderr)
        for failure in failures[: args.max_failures]:
            print(f"  {failure}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
