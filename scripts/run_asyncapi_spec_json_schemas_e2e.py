"""Run generation against the AsyncAPI spec JSON Schemas."""

from __future__ import annotations

import argparse
import ast
import importlib
import json
import re
import sys
import tempfile
import time
from collections.abc import Generator, Iterable, Mapping
from contextlib import contextmanager
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from datamodel_code_generator import InputFileType, generate, load_data

if TYPE_CHECKING:
    from types import ModuleType


STABLE_SCHEMA_NAME_PATTERN = re.compile(r"^\d+\.\d+\.\d+\.json$")


def iter_stable_schema_paths(schemas_root: Path) -> Iterable[Path]:
    """Yield stable AsyncAPI schema version files."""
    yield from sorted(
        (path for path in schemas_root.iterdir() if STABLE_SCHEMA_NAME_PATTERN.fullmatch(path.name)),
        key=lambda path: tuple(int(part) for part in path.stem.split(".")),
    )


def write_local_http_refs(schema_path: Path, local_ref_root: Path) -> None:
    """Mirror URL-keyed definitions for --http-local-ref-path resolution."""
    raw = load_data(schema_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        return
    definitions = raw.get("definitions")
    if not isinstance(definitions, dict):
        return
    for key, value in definitions.items():
        if not isinstance(key, str) or not key.startswith(("http://", "https://")):
            continue
        parsed = urlparse(key)
        if not parsed.netloc:
            continue
        local_path = local_ref_root.joinpath(parsed.netloc, *[part for part in parsed.path.split("/") if part])
        write_generated_module(local_path, json_dumps(value))
        if not local_path.suffix:
            write_generated_module(local_path.with_name(f"{local_path.name}.json"), json_dumps(value))


def json_dumps(value: object) -> str:
    """Dump JSON with deterministic formatting."""
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)


def iter_generated_modules(generated: object) -> Iterable[tuple[tuple[str, ...] | None, str]]:
    """Normalize datamodel-code-generator single and multi-module outputs."""
    if isinstance(generated, str):
        yield None, generated
    elif isinstance(generated, Mapping):
        for raw_module_path, module_code in generated.items():
            module_path = raw_module_path if isinstance(raw_module_path, tuple) else (str(raw_module_path),)
            yield module_path, module_code


@contextmanager
def sys_path_entry(path: str) -> Generator[None]:
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
    with tempfile.TemporaryDirectory(prefix="dcg-asyncapi-schemas-") as temporary_directory:
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
        "schemas_root",
        type=Path,
        help="Path to asyncapi/spec-json-schemas/schemas",
    )
    parser.add_argument(
        "--expected-schemas",
        type=int,
        help="Expected number of stable schema files in the pinned AsyncAPI schema suite.",
    )
    parser.add_argument(
        "--max-failures",
        type=int,
        default=20,
        help="Maximum failures to print before returning a non-zero exit status.",
    )
    return parser.parse_args()


def main() -> int:
    """Run the AsyncAPI spec JSON Schemas e2e check."""
    args = parse_args()
    schemas_root = args.schemas_root.resolve()
    if not schemas_root.is_dir():
        print(f"AsyncAPI spec JSON Schemas directory does not exist: {schemas_root}", file=sys.stderr)
        return 2

    schema_paths = list(iter_stable_schema_paths(schemas_root))
    if args.expected_schemas is not None and len(schema_paths) != args.expected_schemas:
        print(f"Expected {args.expected_schemas} stable schemas, found {len(schema_paths)}.", file=sys.stderr)
        return 1

    failures: list[str] = []
    started_at = time.monotonic()
    for index, schema_path in enumerate(schema_paths, start=1):
        with tempfile.TemporaryDirectory(prefix=f"dcg-asyncapi-http-refs-{index}-") as local_ref_dir:
            local_ref_root = Path(local_ref_dir)
            write_local_http_refs(schema_path, local_ref_root)
            try:
                generated = generate(
                    schema_path,
                    input_file_type=InputFileType.JsonSchema,
                    disable_timestamp=True,
                    formatters=[],
                    http_local_ref_path=local_ref_root,
                )
            except Exception as exc:  # noqa: BLE001
                failures.append(f"{schema_path.name}: generate failed with {type(exc).__name__}: {exc}")
                continue

        generated_modules = list(iter_generated_modules(generated))
        if not generated_modules:
            failures.append(f"{schema_path.name}: generate returned no modules")
            continue

        for module_path, module_code in generated_modules:
            module_name = "/".join(module_path) if module_path else "<single module>"
            if not isinstance(module_code, str) or not module_code.strip():
                failures.append(f"{schema_path.name} {module_name}: generated module is empty")
                continue
            try:
                ast.parse(module_code)
            except SyntaxError as exc:
                failures.append(
                    f"{schema_path.name} {module_name}: invalid Python syntax at {exc.lineno}:{exc.offset}: {exc.msg}"
                )

        module_prefix = f"generated_asyncapi_schema_{index}"
        try:
            import_generated_modules(module_prefix, generated_modules)
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{schema_path.name}: generated modules failed to import: {type(exc).__name__}: {exc}")
        finally:
            remove_imported_modules(module_prefix)

    elapsed = time.monotonic() - started_at
    print(f"checked {len(schema_paths)} stable AsyncAPI spec JSON Schemas in {elapsed:.1f}s")
    if failures:
        print(f"{len(failures)} AsyncAPI spec JSON Schemas e2e failures:", file=sys.stderr)
        for failure in failures[: args.max_failures]:
            print(f"  {failure}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
