"""Tests that e2e test modules use shared assertion helpers."""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING

import pytest

from datamodel_code_generator.http import _get_httpx
from tests.conftest import (
    HttpxGetMockFactory,
    MockHttpxResponse,
    assert_httpx_get_kwargs,
    create_httpx_get_mock,
)
from tests.main.conftest import (
    _assert_generated_package_model_validation,
    assert_generated_model_json_invalid,
    assert_generated_model_json_validation,
    run_main_and_assert,
)

if TYPE_CHECKING:
    from collections.abc import Iterable

    from pytest_mock import MockerFixture

TESTS_ROOT = Path(__file__).parent
DIRECT_ASSERT_EXEMPT_FILES_INI = "assert_helper_direct_assert_exempt_files"
DIRECT_ASSERT_FAILURE_MESSAGE = (
    "Direct assert statements in guarded test modules require explicit permission.\n"
    "HTTP, external-request, or similar mock assertions may be unavoidable, but generation tests should generally "
    "be e2e tests that compare generated output through the shared assert helpers.\n"
    "Use @pytest.mark.allow_direct_assert for a narrow exception, or add intentional legacy/unit files to "
    f"{DIRECT_ASSERT_EXEMPT_FILES_INI}."
)
HELPER_ROOT = Path("data", "python")
PRIVATE_TARGET_MODULES = (
    "datamodel_code_generator._api_generation",
    "datamodel_code_generator._fastapi",
    "datamodel_code_generator._target_config",
)
PRIVATE_TARGET_FAILURE_MESSAGE = (
    "Generation target tests and their report helpers reach a target through its public entry points, the command "
    "line or datamodel_code_generator.fastapi, as the model tests reach model generation.\n"
    "Patch a private function by its dotted name only to inject an abnormal path."
)


@dataclass(frozen=True)
class DirectAssert:
    """A direct assert statement in a guarded test module."""

    path: Path
    function_name: str | None
    lineno: int
    statement: str


@dataclass(frozen=True)
class WholeSysModulesPatch:
    """A patch.dict call that replaces the complete module registry on teardown."""

    path: Path
    lineno: int
    statement: str


def _attribute_chain(node: ast.AST) -> tuple[str, ...]:
    if isinstance(node, ast.Name):
        return (node.id,)
    if not isinstance(node, ast.Attribute) or not (parent := _attribute_chain(node.value)):
        return ()
    return (*parent, node.attr)


def _module_registry_aliases(tree: ast.Module) -> tuple[set[str], set[str], set[str], set[str], set[str]]:
    patch_names: set[str] = set()
    sys_names: set[str] = set()
    module_names: set[str] = set()
    mock_names: set[str] = set()
    unittest_names: set[str] = set()
    for node in ast.walk(tree):
        match node:
            case ast.Import(names=names):
                sys_names.update(alias.asname or "sys" for alias in names if alias.name == "sys")
                mock_names.update(alias.asname for alias in names if alias.name == "unittest.mock" and alias.asname)
                unittest_names.update(alias.asname or "unittest" for alias in names if alias.name == "unittest")
                if any(alias.name == "unittest.mock" and not alias.asname for alias in names):
                    unittest_names.add("unittest")
            case ast.ImportFrom(module="sys", names=names):
                module_names.update(alias.asname or "modules" for alias in names if alias.name == "modules")
            case ast.ImportFrom(module="unittest", names=names):
                mock_names.update(alias.asname or "mock" for alias in names if alias.name == "mock")
            case ast.ImportFrom(module="unittest.mock", names=names):
                patch_names.update(alias.asname or "patch" for alias in names if alias.name == "patch")
            case _:
                pass
    return patch_names, sys_names, module_names, mock_names, unittest_names


def _is_whole_sys_modules_patch(
    call: ast.Call,
    *,
    patch_names: set[str],
    sys_names: set[str],
    module_names: set[str],
    mock_names: set[str],
    unittest_names: set[str],
) -> bool:
    function_chain = _attribute_chain(call.func)
    match function_chain:
        case (patch_name, "dict") if patch_name in patch_names:
            pass
        case ("mocker", "patch", "dict"):
            pass
        case (mock_name, "patch", "dict") if mock_name in mock_names:
            pass
        case (unittest_name, "mock", "patch", "dict") if unittest_name in unittest_names:
            pass
        case _:
            return False

    if call.args:
        target = call.args[0]
    elif (target_keyword := next((keyword for keyword in call.keywords if keyword.arg == "in_dict"), None)) is not None:
        target = target_keyword.value
    else:
        return False

    if isinstance(target, ast.Constant):
        return target.value == "sys.modules"
    if isinstance(target, ast.Name):
        return target.id in module_names
    if not isinstance(target, ast.Attribute):
        return False
    target_chain = _attribute_chain(target)
    return len(target_chain) == 2 and target_chain[0] in sys_names and target_chain[1] == "modules"


def _collect_whole_sys_modules_patches(path: Path, tests_root: Path = TESTS_ROOT) -> list[WholeSysModulesPatch]:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    patch_names, sys_names, module_names, mock_names, unittest_names = _module_registry_aliases(tree)
    return [
        WholeSysModulesPatch(
            path=path.relative_to(tests_root),
            lineno=node.lineno,
            statement=" ".join((ast.get_source_segment(source, node) or ast.unparse(node)).split()),
        )
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and _is_whole_sys_modules_patch(
            node,
            patch_names=patch_names,
            sys_names=sys_names,
            module_names=module_names,
            mock_names=mock_names,
            unittest_names=unittest_names,
        )
    ]


def _allows_direct_assert(function: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    return any(
        ast.unparse(decorator).split("(", 1)[0] == "pytest.mark.allow_direct_assert"
        for decorator in function.decorator_list
    )


def _is_test_file(path: Path, tests_root: Path) -> bool:
    relative_path = path.relative_to(tests_root)
    is_test_file = False
    match relative_path.parts:
        case ("data", *_):
            pass
        case (*_, file_name) if path.suffix == ".py" and (
            file_name.startswith("test_") or file_name.endswith("_test.py")
        ):
            is_test_file = True
        case _:
            pass
    return is_test_file


def _normalize_exempt_file(raw_path: str) -> Path:
    path = Path(raw_path.strip())
    normalized_path = path
    match path.parts:
        case ("tests", *parts):
            normalized_path = Path(*parts)
        case _:
            pass
    return normalized_path


def _configured_exempt_files(config: pytest.Config) -> frozenset[Path]:
    return frozenset(
        normalized_path
        for raw_path in config.getini(DIRECT_ASSERT_EXEMPT_FILES_INI)
        if raw_path.strip() and (normalized_path := _normalize_exempt_file(raw_path))
    )


def _iter_guarded_test_files(tests_root: Path, exempt_files: Iterable[Path]) -> Iterable[Path]:
    exempt_file_set = frozenset(exempt_files)
    for path in sorted(tests_root.rglob("*.py")):
        if not _is_test_file(path, tests_root):
            continue
        if path.relative_to(tests_root) in exempt_file_set:
            continue
        yield path


def _statement(node: ast.Assert, source: str) -> str:
    statement = ast.get_source_segment(source, node) or ast.unparse(node)
    return " ".join(statement.split())


class _DirectAssertVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.asserts: list[ast.Assert] = []

    def visit_Assert(self, node: ast.Assert) -> None:
        self.asserts.append(node)

    def visit_FunctionDef(self, _node: ast.FunctionDef) -> None:
        return

    def visit_AsyncFunctionDef(self, _node: ast.AsyncFunctionDef) -> None:
        return

    def visit_ClassDef(self, _node: ast.ClassDef) -> None:
        return


def _collect_function_asserts(function: ast.FunctionDef | ast.AsyncFunctionDef) -> list[ast.Assert]:
    visitor = _DirectAssertVisitor()
    for statement in function.body:
        visitor.visit(statement)
    return visitor.asserts


def _collect_direct_asserts(path: Path, tests_root: Path = TESTS_ROOT) -> list[DirectAssert]:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    direct_asserts: list[DirectAssert] = []

    direct_asserts.extend(
        DirectAssert(
            path=path.relative_to(tests_root),
            function_name="<module>",
            lineno=node.lineno,
            statement=_statement(node, source),
        )
        for node in tree.body
        if isinstance(node, ast.Assert)
    )

    for function in [node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]:
        if _allows_direct_assert(function):
            continue
        direct_asserts.extend(
            DirectAssert(
                path=path.relative_to(tests_root),
                function_name=function.name,
                lineno=node.lineno,
                statement=_statement(node, source),
            )
            for node in _collect_function_asserts(function)
        )

    return direct_asserts


def _collect_guarded_direct_asserts(tests_root: Path, exempt_files: Iterable[Path]) -> list[DirectAssert]:
    return [
        direct_assert
        for path in _iter_guarded_test_files(tests_root, exempt_files)
        if (path_direct_asserts := _collect_direct_asserts(path, tests_root))
        for direct_assert in path_direct_asserts
    ]


def _format_direct_assert_failure(direct_asserts: Iterable[DirectAssert]) -> str:
    details = "\n".join(
        f"  tests/{direct_assert.path}:{direct_assert.lineno} "
        f"({direct_assert.function_name}): {direct_assert.statement}"
        for direct_assert in direct_asserts
    )
    return f"{DIRECT_ASSERT_FAILURE_MESSAGE}\n{details}"


def _collect_helper_direct_asserts(tests_root: Path) -> list[DirectAssert]:
    return [
        direct_assert
        for path in sorted(tests_root.joinpath(HELPER_ROOT).rglob("*.py"))
        for direct_assert in _collect_direct_asserts(path, tests_root)
    ]


def _imported_modules(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    if isinstance(node, ast.ImportFrom) and node.module is not None and node.level == 0:
        return [node.module, *(f"{node.module}.{alias.name}" for alias in node.names)]
    return []


def _is_private_target(module: str) -> bool:
    return any(module == prefix or module.startswith(f"{prefix}.") for prefix in PRIVATE_TARGET_MODULES)


def _collect_private_target_imports(tests_root: Path) -> list[str]:
    paths = [
        *(path for path in sorted(tests_root.rglob("*.py")) if _is_test_file(path, tests_root)),
        *sorted(tests_root.joinpath(HELPER_ROOT).rglob("*.py")),
    ]
    return [
        f"  tests/{path.relative_to(tests_root).as_posix()}:{node.lineno}: {module}"
        for path in paths
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"), filename=str(path)))
        if (module := next((name for name in _imported_modules(node) if _is_private_target(name)), None))
    ]


def test_modules_use_shared_assertion_helpers(pytestconfig: pytest.Config) -> None:
    """Direct asserts in guarded test modules must be explicitly marked as exceptions."""
    direct_asserts = _collect_guarded_direct_asserts(TESTS_ROOT, _configured_exempt_files(pytestconfig))

    if not direct_asserts:
        return

    pytest.fail(_format_direct_assert_failure(direct_asserts), pytrace=False)  # pragma: no cover


def test_helpers_use_shared_assertion_helpers() -> None:
    """Report helpers under tests/data/python leave every comparison to the shared assert helpers, as tests do."""
    if not (direct_asserts := _collect_helper_direct_asserts(TESTS_ROOT)):
        return
    pytest.fail(_format_direct_assert_failure(direct_asserts), pytrace=False)  # pragma: no cover


def test_modules_reach_generation_targets_through_public_entry_points() -> None:
    """Tests and report helpers import no private target coordinator, target, or target settings module."""
    if not (imports := _collect_private_target_imports(TESTS_ROOT)):
        return
    pytest.fail("\n".join((PRIVATE_TARGET_FAILURE_MESSAGE, *imports)), pytrace=False)  # pragma: no cover


def test_collect_helper_direct_asserts_reports_helper_assert(tmp_path: Path) -> None:
    """A direct assert in a report helper is reported, while other test data is left alone."""
    helper = tmp_path / "data" / "python" / "report.py"
    helper.parent.mkdir(parents=True)
    helper.write_text("def report(value):\n    assert value\n    return value\n", encoding="utf-8")
    (tmp_path / "data" / "expected.py").write_text("assert True\n", encoding="utf-8")

    assert _collect_helper_direct_asserts(tmp_path) == [
        DirectAssert(Path("data/python/report.py"), "report", 2, "assert value")
    ]


def test_collect_private_target_imports_reports_every_form(tmp_path: Path) -> None:
    """Every import form of a private target module is reported in test modules and report helpers only."""
    (tmp_path / "test_target.py").write_text(
        "import datamodel_code_generator._api_generation\n"
        "from datamodel_code_generator._fastapi.target import FastAPITarget\n"
        "from datamodel_code_generator import _target_config, _api_publication\n"
        "from datamodel_code_generator.fastapi import generate_fastapi\n"
        "from . import sibling\n",
        encoding="utf-8",
    )
    helper = tmp_path / "data" / "python" / "helper.py"
    helper.parent.mkdir(parents=True)
    helper.write_text("from datamodel_code_generator._fastapi import config\n", encoding="utf-8")
    fixture = tmp_path / "data" / "boundaries" / "forbidden.py"
    fixture.parent.mkdir(parents=True)
    fixture.write_text("from datamodel_code_generator._api_generation import render_target\n", encoding="utf-8")

    assert _collect_private_target_imports(tmp_path) == [
        "  tests/test_target.py:1: datamodel_code_generator._api_generation",
        "  tests/test_target.py:2: datamodel_code_generator._fastapi.target",
        "  tests/test_target.py:3: datamodel_code_generator._target_config",
        "  tests/data/python/helper.py:1: datamodel_code_generator._fastapi",
    ]


def test_modules_never_patch_the_complete_module_registry() -> None:
    """Module import seams must restore only exact entries; imported aliases stay reserved when shadowed."""
    whole_registry_patches = [
        whole_registry_patch
        for path in sorted(TESTS_ROOT.rglob("*.py"))
        if _is_test_file(path, TESTS_ROOT)
        for whole_registry_patch in _collect_whole_sys_modules_patches(path, TESTS_ROOT)
    ]
    if not whole_registry_patches:
        return

    details = "\n".join(f"  tests/{patch.path}:{patch.lineno}: {patch.statement}" for patch in whole_registry_patches)
    pytest.fail(  # pragma: no cover
        "patch.dict(sys.modules, ...) restores the complete module registry and invalidates lazy-import caches. "
        "For deterministic static enforcement, imported sys/patch aliases are reserved across every scope, "
        "including when shadowed. "
        "Use monkeypatch.setitem(sys.modules, name, value) so only the intended entry is restored.\n"
        f"{details}",
        pytrace=False,
    )


def test_modules_never_patch_the_complete_module_registry_reports_details(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unsafe registry patches report the exact test location and safe replacement."""
    test_file = tmp_path / "test_unsafe.py"
    test_file.write_text(
        """\
import sys
from unittest.mock import patch

patch.dict(sys.modules, {"unsafe": None})
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(sys.modules[__name__], "TESTS_ROOT", tmp_path)

    with pytest.raises(pytest.fail.Exception, match=r"(?s)monkeypatch\.setitem.*tests/test_unsafe\.py:4"):
        test_modules_never_patch_the_complete_module_registry()


def test_configured_exempt_files_exist(pytestconfig: pytest.Config) -> None:
    """Configured direct-assert exemptions must point to existing test files."""
    if not (
        missing := sorted(path for path in _configured_exempt_files(pytestconfig) if not (TESTS_ROOT / path).is_file())
    ):
        return

    pytest.fail(  # pragma: no cover
        "assert_helper_direct_assert_exempt_files contains missing files:\n"
        + "\n".join(f"  - tests/{path}" for path in missing),
        pytrace=False,
    )


class _MissingExemptConfig:
    def getini(self, name: str) -> list[str]:
        if name == DIRECT_ASSERT_EXEMPT_FILES_INI:
            return ["missing.py"]
        raise KeyError(name)  # pragma: no cover


def test_configured_exempt_files_exist_reports_missing_file() -> None:
    """Missing configured direct-assert exemptions are reported clearly."""
    with pytest.raises(pytest.fail.Exception, match=r"tests/missing\.py"):
        test_configured_exempt_files_exist(_MissingExemptConfig())  # type: ignore[arg-type]


def test_modules_use_shared_assertion_helpers_reports_unmarked_assert(
    tmp_path: Path,
) -> None:
    """Unmarked direct asserts in guarded files are reported as test failures."""
    test_file = tmp_path / "test_example.py"
    test_file.write_text("def test_example():\n    assert False\n", encoding="utf-8")
    direct_asserts = _collect_guarded_direct_asserts(tmp_path, ())

    with pytest.raises(pytest.fail.Exception, match="shared assert helpers"):
        pytest.fail(_format_direct_assert_failure(direct_asserts), pytrace=False)


def test_collect_whole_sys_modules_patches_covers_supported_targets(tmp_path: Path) -> None:
    """The registry guard recognizes string, attribute, alias, and keyword targets."""
    test_file = tmp_path / "test_module_registry.py"
    test_file.write_text(
        """\
import sys as system
from sys import modules as registry
from unittest.mock import patch as patcher

patcher.dict("sys.modules", {"one": None})
mocker.patch.dict(system.modules, {"two": None})
patcher.dict(registry, {"three": None})
patcher.dict(in_dict="sys.modules", values={"four": None})
monkeypatch.setitem(system.modules, "safe", None)
patcher.dict("other.registry", {"safe": None})
dict()
""",
        encoding="utf-8",
    )

    assert _collect_whole_sys_modules_patches(test_file, tmp_path) == [
        WholeSysModulesPatch(
            Path("test_module_registry.py"),
            5,
            'patcher.dict("sys.modules", {"one": None})',
        ),
        WholeSysModulesPatch(
            Path("test_module_registry.py"),
            6,
            'mocker.patch.dict(system.modules, {"two": None})',
        ),
        WholeSysModulesPatch(
            Path("test_module_registry.py"),
            7,
            'patcher.dict(registry, {"three": None})',
        ),
        WholeSysModulesPatch(
            Path("test_module_registry.py"),
            8,
            'patcher.dict(in_dict="sys.modules", values={"four": None})',
        ),
    ]


def test_collect_whole_sys_modules_patches_covers_local_aliases(tmp_path: Path) -> None:
    """Aliases imported in function scope and exact mocker calls are guarded."""
    test_file = tmp_path / "test_local_module_registry.py"
    test_file.write_text(
        """\
def test_local_aliases(mocker):
    import sys as system
    from sys import modules as registry
    from unittest import mock as mock_module
    from unittest.mock import patch as patcher
    import unittest as unit
    import unittest.mock
    import unittest.mock as imported_mock
    patcher.dict(in_dict=system.modules, values={"one": None})
    mock_module.patch.dict(registry, {"two": None})
    mocker.patch.dict("sys.modules", {"three": None})
    unit.mock.patch.dict(system.modules, {"four": None})
    unittest.mock.patch.dict(registry, {"five": None})
    imported_mock.patch.dict(system.modules, {"six": None})
""",
        encoding="utf-8",
    )

    assert _collect_whole_sys_modules_patches(test_file, tmp_path) == [
        WholeSysModulesPatch(
            Path("test_local_module_registry.py"),
            9,
            'patcher.dict(in_dict=system.modules, values={"one": None})',
        ),
        WholeSysModulesPatch(
            Path("test_local_module_registry.py"),
            10,
            'mock_module.patch.dict(registry, {"two": None})',
        ),
        WholeSysModulesPatch(
            Path("test_local_module_registry.py"),
            11,
            'mocker.patch.dict("sys.modules", {"three": None})',
        ),
        WholeSysModulesPatch(
            Path("test_local_module_registry.py"),
            12,
            'unit.mock.patch.dict(system.modules, {"four": None})',
        ),
        WholeSysModulesPatch(
            Path("test_local_module_registry.py"),
            13,
            'unittest.mock.patch.dict(registry, {"five": None})',
        ),
        WholeSysModulesPatch(
            Path("test_local_module_registry.py"),
            14,
            'imported_mock.patch.dict(system.modules, {"six": None})',
        ),
    ]


def test_collect_whole_sys_modules_patches_ignores_unrelated_calls(tmp_path: Path) -> None:
    """Unrelated patch-like APIs, targets, and exact-entry monkeypatches remain allowed."""
    test_file = tmp_path / "test_unrelated_module_registry.py"
    test_file.write_text(
        """\
import sys
from unittest.mock import patch

foo.patch.dict(sys.modules, {"safe": None})
factory().patch.dict(sys.modules, {"safe": None})
patch.dict("other.registry", {"safe": None})
patch.dict(factory(), {"safe": None})
patch.dict()
monkeypatch.setitem(sys.modules, "safe", None)
""",
        encoding="utf-8",
    )

    assert _collect_whole_sys_modules_patches(test_file, tmp_path) == []


def test_iter_guarded_test_files_uses_all_test_files_by_default(tmp_path: Path) -> None:
    """All pytest-style test files under tests/ are guarded unless configured otherwise."""
    (tmp_path / "test_root.py").write_text("", encoding="utf-8")
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / "example_test.py").write_text("", encoding="utf-8")
    (tmp_path / "nested" / "helper.py").write_text("", encoding="utf-8")
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "test_fixture.py").write_text("", encoding="utf-8")

    assert [path.relative_to(tmp_path) for path in _iter_guarded_test_files(tmp_path, ())] == [
        Path("nested/example_test.py"),
        Path("test_root.py"),
    ]


def test_iter_guarded_test_files_skips_configured_exempt_files(tmp_path: Path) -> None:
    """Configured file exceptions are skipped by the guard."""
    (tmp_path / "test_guarded.py").write_text("", encoding="utf-8")
    (tmp_path / "test_exempt.py").write_text("", encoding="utf-8")

    assert [path.relative_to(tmp_path) for path in _iter_guarded_test_files(tmp_path, (Path("test_exempt.py"),))] == [
        Path("test_guarded.py")
    ]


@pytest.mark.parametrize(
    ("raw_path", "expected"),
    [
        ("test_unit.py", Path("test_unit.py")),
        ("tests/test_unit.py", Path("test_unit.py")),
        ("tests/main/test_unit.py", Path("main/test_unit.py")),
    ],
)
def test_normalize_exempt_file_accepts_tests_prefix(raw_path: str, expected: Path) -> None:
    """Config paths may be relative to tests/ or include the tests/ prefix."""
    assert _normalize_exempt_file(raw_path) == expected


def test_collect_direct_asserts_reports_module_level_assert(tmp_path: Path) -> None:
    """Module-level direct asserts are reported by the guard."""
    test_file = tmp_path / "test_example.py"
    test_file.write_text("assert False\n", encoding="utf-8")

    assert _collect_direct_asserts(test_file, tmp_path) == [
        DirectAssert(Path("test_example.py"), "<module>", 1, "assert False")
    ]


def test_collect_function_asserts_ignores_nested_async_helpers() -> None:
    """Nested async helpers are reported on their own function, not the outer test."""
    module = ast.parse(
        """
def test_example():
    async def assert_later():
        assert False
"""
    )
    outer_function = next(node for node in ast.walk(module) if isinstance(node, ast.FunctionDef))
    inner_function = next(node for node in ast.walk(module) if isinstance(node, ast.AsyncFunctionDef))

    assert _collect_function_asserts(outer_function) == []
    assert len(_collect_function_asserts(inner_function)) == 1


def test_collect_function_asserts_ignores_nested_classes() -> None:
    """Nested classes are reported on their own methods, not the outer test."""
    module = ast.parse(
        """
def test_example():
    class AssertsLater:
        def assert_later(self):
            assert False
"""
    )
    outer_function = next(node for node in ast.walk(module) if isinstance(node, ast.FunctionDef))
    inner_method = next(
        node for node in ast.walk(module) if isinstance(node, ast.FunctionDef) and node.name == "assert_later"
    )

    assert _collect_function_asserts(outer_function) == []
    assert len(_collect_function_asserts(inner_method)) == 1


def test_assert_httpx_get_kwargs_accepts_expected_urls_with_explicit_call_count(mocker: MockerFixture) -> None:
    """Explicit call_count works with multi-URL HTTP client assertions."""
    mock_get = create_httpx_get_mock(mocker)
    mock_get(
        "https://example.com/person.json",
        headers=None,
        verify=True,
        follow_redirects=True,
        params=None,
        timeout=30.0,
    )
    mock_get(
        "https://example.com/address.json",
        headers=None,
        verify=True,
        follow_redirects=True,
        params=None,
        timeout=30.0,
    )

    assert_httpx_get_kwargs(
        mock_get,
        expected_urls=["https://example.com/person.json", "https://example.com/address.json"],
        call_count=2,
    )


def test_assert_httpx_get_kwargs_accepts_called_true(mocker: MockerFixture) -> None:
    """called=True asserts that at least one URL request was made."""
    mock_get = create_httpx_get_mock(mocker)
    mock_get(
        "https://example.com/person.json",
        headers=None,
        verify=True,
        follow_redirects=True,
        params=None,
        timeout=30.0,
    )

    assert_httpx_get_kwargs(mock_get, called=True)


def test_assert_httpx_get_kwargs_validates_call_options_for_every_call(mocker: MockerFixture) -> None:
    """HTTP option checks apply to all recorded URL requests."""
    mock_get = create_httpx_get_mock(mocker)
    expected_headers = [("Authorization", "Bearer token")]
    expected_params = [("version", "v2")]
    mock_get(
        "https://example.com/person.json",
        headers=expected_headers,
        verify=False,
        follow_redirects=True,
        params=expected_params,
        timeout=60.0,
    )
    mock_get(
        "https://example.com/address.json",
        headers=expected_headers,
        verify=False,
        follow_redirects=True,
        params=expected_params,
        timeout=60.0,
    )

    assert_httpx_get_kwargs(
        mock_get,
        headers=expected_headers,
        params=expected_params,
        verify=False,
        timeout=60.0,
    )


def test_assert_httpx_get_kwargs_reports_call_option_mismatch_per_call(mocker: MockerFixture) -> None:
    """HTTP option failures catch mismatches before the last URL request."""
    mock_get = create_httpx_get_mock(mocker)
    mock_get(
        "https://example.com/person.json",
        headers=[("Authorization", "Bearer old")],
        verify=True,
        follow_redirects=True,
        params=None,
        timeout=30.0,
    )
    mock_get(
        "https://example.com/address.json",
        headers=[("Authorization", "Bearer token")],
        verify=True,
        follow_redirects=True,
        params=None,
        timeout=30.0,
    )

    with pytest.raises(AssertionError):
        assert_httpx_get_kwargs(mock_get, headers=[("Authorization", "Bearer token")])


def test_assert_httpx_get_kwargs_validates_params_contains_for_every_call(mocker: MockerFixture) -> None:
    """Subset query parameter checks apply to all recorded URL requests."""
    mock_get = create_httpx_get_mock(mocker)
    mock_get(
        "https://example.com/person.json",
        headers=None,
        verify=True,
        follow_redirects=True,
        params=[("version", "v2"), ("format", "json")],
        timeout=30.0,
    )
    mock_get(
        "https://example.com/address.json",
        headers=None,
        verify=True,
        follow_redirects=True,
        params=[("version", "v2"), ("format", "yaml")],
        timeout=30.0,
    )

    assert_httpx_get_kwargs(
        mock_get,
        expected_urls=["https://example.com/person.json", "https://example.com/address.json"],
        params_contains={"version": "v2"},
    )


def test_assert_httpx_get_kwargs_reports_params_contains_mismatch_per_call(mocker: MockerFixture) -> None:
    """Subset query parameter failures identify the request that mismatched."""
    mock_get = create_httpx_get_mock(mocker)
    mock_get(
        "https://example.com/person.json",
        headers=None,
        verify=True,
        follow_redirects=True,
        params=[("version", "v2")],
        timeout=30.0,
    )
    mock_get(
        "https://example.com/address.json",
        headers=None,
        verify=True,
        follow_redirects=True,
        params=[("version", "v1")],
        timeout=30.0,
    )

    with pytest.raises(AssertionError, match="call 2"):
        assert_httpx_get_kwargs(mock_get, params_contains={"version": "v2"})


def test_mock_httpx_get_returns_response_for_registered_url(mock_httpx_get: HttpxGetMockFactory) -> None:
    """URL-bound HTTP mocks return fixture content for the registered URL."""
    mock_httpx_get(MockHttpxResponse("https://example.com/schema.json", '{"type": "object"}'))

    response = _get_httpx().get("https://example.com/schema.json")

    assert response.text == '{"type": "object"}'


def test_mock_httpx_get_rejects_unregistered_url(mock_httpx_get: HttpxGetMockFactory) -> None:
    """URL-bound HTTP mocks fail when code fetches an unexpected URL."""
    mock_httpx_get(MockHttpxResponse("https://example.com/schema.json", '{"type": "object"}'))

    with pytest.raises(pytest.fail.Exception, match="Unexpected HTTP client URL"):
        _get_httpx().get("https://example.com/other.json")


def test_assert_generated_model_json_validation_without_attribute_check(tmp_path: Path) -> None:
    """Generated-model validation helper supports tests that only check rejection type."""
    output_path = tmp_path / "generated_model.py"
    output_path.write_text(
        "from pydantic import BaseModel\n\nclass Model(BaseModel):\n    name: str\n",
        encoding="utf-8",
    )

    assert_generated_model_json_validation(
        output_path,
        module_name="generated_model_without_attribute_check",
        model_name="Model",
        valid_json='{"name": "x"}',
        invalid_json='{"name": 1}',
        expected_error_type="string_type",
    )


def test_generated_model_json_helpers_restore_existing_module(tmp_path: Path) -> None:
    """Generated-model validation helpers restore an existing sys.modules entry."""
    output_path = tmp_path / "generated_model.py"
    output_path.write_text(
        "from pydantic import BaseModel\n\nclass Model(BaseModel):\n    value: int\n",
        encoding="utf-8",
    )
    module_name = "preexisting_generated_model"
    previous_module = ModuleType(module_name)
    sys.modules[module_name] = previous_module

    try:
        assert_generated_model_json_validation(
            output_path,
            module_name=module_name,
            model_name="Model",
            valid_json='{"value": 1}',
            invalid_json='{"value": "bad"}',
            expected_error_type="int_parsing",
        )
        assert sys.modules[module_name] is previous_module

        assert_generated_model_json_invalid(
            output_path,
            module_name=module_name,
            model_name="Model",
            invalid_json='{"value": "bad"}',
            expected_error_type="int_parsing",
        )
        assert sys.modules[module_name] is previous_module
    finally:
        sys.modules.pop(module_name, None)


def test_generated_package_model_validation_restores_existing_modules(tmp_path: Path) -> None:
    """Generated-package validation restores existing package modules."""
    output_dir = tmp_path / "model"
    output_dir.mkdir()
    (output_dir / "__init__.py").write_text("", encoding="utf-8")
    (output_dir / "target.py").write_text(
        "from pydantic import BaseModel\n\nclass Target(BaseModel):\n    value: int\n",
        encoding="utf-8",
    )

    previous_package = ModuleType("model")
    previous_module = ModuleType("model.target")
    sys.modules["model"] = previous_package
    sys.modules["model.target"] = previous_module

    try:
        _assert_generated_package_model_validation(
            output_dir,
            module_path="target",
            model_name="Target",
            data={"value": 1},
        )
        assert sys.modules["model"] is previous_package
        assert sys.modules["model.target"] is previous_module
    finally:
        sys.modules.pop("model", None)
        sys.modules.pop("model.target", None)


@pytest.mark.skipif(sys.version_info >= (3, 14), reason="requires a target version newer than the runtime")
def test_run_main_runtime_validation_skips_newer_python_target(tmp_path: Path) -> None:
    """Runtime validation follows generated-code compatibility guards."""
    input_path = tmp_path / "schema.json"
    input_path.write_text('{"type": "object", "properties": {"value": {"type": "integer"}}}', encoding="utf-8")

    run_main_and_assert(
        input_path=input_path,
        output_path=tmp_path / "model.py",
        input_file_type="jsonschema",
        extra_args=[
            "--target-python-version",
            "3.14",
            "--formatters",
            "builtin",
        ],
        skip_code_validation=True,
        runtime_validation_module="missing_module",
        runtime_validation_model_name="MissingModel",
        runtime_validation_data={},
    )
