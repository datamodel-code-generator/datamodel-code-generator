"""Tests for package metadata and source distribution configuration."""

from __future__ import annotations

import ast
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, cast

import pytest

from datamodel_code_generator.util import load_toml
from tests.conftest import assert_output

if TYPE_CHECKING:
    from collections.abc import Iterator

ROOT = Path(__file__).resolve().parents[1]
TESTS_ROOT = ROOT / "tests"
EXPECTED_PACKAGE_METADATA_PATH = ROOT / "tests" / "data" / "expected" / "package_metadata"
EXCLUDED_IMPORT_ROOTS = frozenset({"datamodel_code_generator", "tests"})


def _load_pyproject() -> dict[str, Any]:
    return cast("dict[str, Any]", load_toml(ROOT / "pyproject.toml"))


def _sdist_include_root(entry: str) -> str | None:
    match PurePosixPath(entry).parts:
        case ("/", root, *_):
            return root
        case (root, *_) if root:
            return root
        case _:
            return None
    return None


def _sdist_include_roots() -> frozenset[str]:
    includes = cast(
        "list[str]",
        _load_pyproject()["tool"]["hatch"]["build"]["targets"]["sdist"]["include"],
    )
    return frozenset(root for entry in includes if (root := _sdist_include_root(entry)))


def _is_test_source(path: Path) -> bool:
    match path.relative_to(TESTS_ROOT).parts:
        case ("data", *_):
            return False
        case (*_, file_name) if path.suffix == ".py" and (
            file_name.startswith("test_") or file_name.endswith("_test.py")
        ):
            return True
        case _:
            return False
    return False


def _iter_test_sources() -> Iterator[Path]:
    yield from (path for path in sorted(TESTS_ROOT.rglob("*.py")) if _is_test_source(path))


def _iter_import_roots(path: Path) -> Iterator[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        match node:
            case ast.Import(names=names):
                yield from (alias.name.partition(".")[0] for alias in names)
            case ast.ImportFrom(level=0, module=module) if module:
                yield module.partition(".")[0]
            case _:
                continue


def _is_local_support_import(root: str) -> bool:
    if root in EXCLUDED_IMPORT_ROOTS:
        return False

    return (ROOT / root).is_dir() or (ROOT / f"{root}.py").is_file()


def _local_test_support_import_roots() -> list[str]:
    return sorted({
        root for path in _iter_test_sources() for root in _iter_import_roots(path) if _is_local_support_import(root)
    })


def test_sdist_include_root_normalizes_entries() -> None:
    """Sdist include paths normalize to their top-level archive roots."""
    output = "\n".join(
        f"{entry}: {_sdist_include_root(entry)}"
        for entry in [
            "/scripts",
            "scripts/helpers",
            "",
        ]
    )

    assert_output(f"{output}\n", EXPECTED_PACKAGE_METADATA_PATH / "sdist_include_roots.txt")


def test_sdist_includes_test_support_imports() -> None:
    """Top-level helper packages imported by tests must ship with the sdist."""
    import_roots = _local_test_support_import_roots()

    assert_output(
        "\n".join(import_roots) + "\n",
        EXPECTED_PACKAGE_METADATA_PATH / "test_support_import_roots.txt",
    )

    if not (missing_roots := [root for root in import_roots if root not in _sdist_include_roots()]):
        return

    pytest.fail(  # pragma: no cover
        "Tests import top-level support packages missing from tool.hatch.build.targets.sdist.include:\n"
        + "\n".join(f"  - /{root}" for root in missing_roots),
        pytrace=False,
    )


def test_formatter_extras_preserve_required_ranges_and_markers() -> None:
    """The migration extras exactly duplicate the current required formatter constraints."""
    project = _load_pyproject()["project"]
    assert_output(
        "\n".join(
            f"{name}: required={','.join(item for item in project['dependencies'] if item.startswith(name + '>='))}; "
            f"extra={','.join(project['optional-dependencies'][name])}; "
            f"all={f'datamodel-code-generator[{name}]' in project['optional-dependencies']['all']}"
            for name in ("black", "isort")
        ),
        EXPECTED_PACKAGE_METADATA_PATH / "formatter_extras.txt",
    )
