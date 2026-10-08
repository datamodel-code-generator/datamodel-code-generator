"""Tests that validate generated code execution on the current Python version.

These tests specifically target the current Python runtime to catch runtime errors
that may not be caught when --target-python-version differs from the test environment.

See: _should_skip_exec() in conftest.py for the skip logic that these tests bypass.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

from datamodel_code_generator.format import PythonVersion, is_supported_in_black

from . import conftest as main_conftest
from .conftest import (
    CURRENT_PYTHON_VERSION,
    JSON_SCHEMA_DATA_PATH,
    OPEN_API_DATA_PATH,
    _try_import_generated_output_in_subinterpreter,
    _validate_output_files,
    get_current_version_args,
    run_main_and_assert,
)

_CURRENT_PY_VERSION = PythonVersion(CURRENT_PYTHON_VERSION)
_SKIP_BLACK = pytest.mark.skipif(
    not is_supported_in_black(_CURRENT_PY_VERSION),
    reason=f"Installed black doesn't support Python {CURRENT_PYTHON_VERSION}",
)


class _FakeInterpreter:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.executed_code = ""
        self.closed = False

    def exec(self, code: str) -> None:
        self.executed_code = code
        if self.error is not None:
            raise self.error

    def close(self) -> None:
        self.closed = True


class _FakeInterpreters:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.instances: list[_FakeInterpreter] = []

    def create(self) -> _FakeInterpreter:
        interpreter = _FakeInterpreter(self.error)
        self.instances.append(interpreter)
        return interpreter


@pytest.fixture(autouse=True)
def _reset_subinterpreter_unsupported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main_conftest, "_SUBINTERPRETER_UNSUPPORTED", [])


@_SKIP_BLACK
def test_openapi_api_exec_current_version(output_file: Path) -> None:
    """Test that api.yaml schema generates executable code on current Python."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "api.yaml",
        output_path=output_file,
        extra_args=get_current_version_args("--output-model-type", "pydantic_v2.BaseModel"),
        skip_code_validation=False,
        force_exec_validation=True,
    )


@_SKIP_BLACK
def test_openapi_modular_relative_output_exec_current_version(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that a relative package output is imported from the working directory of each run."""
    for name in ("first", "second"):
        (tmp_path / name).mkdir()
        monkeypatch.chdir(tmp_path / name)
        run_main_and_assert(
            input_path=OPEN_API_DATA_PATH / "modular.yaml",
            output_path=Path("model"),
            extra_args=get_current_version_args("--output-model-type", "pydantic_v2.BaseModel"),
            skip_code_validation=False,
        )
        shutil.rmtree("model")


@_SKIP_BLACK
def test_jsonschema_nested_packages_exec_current_version(tmp_path: Path) -> None:
    """Test that the nested packages of each output are imported from that output."""
    for name in ("exact_imports_collapse_root_models_title", "exact_imports_collapse_root_models"):
        (tmp_path / name).mkdir()
        run_main_and_assert(
            input_path=JSON_SCHEMA_DATA_PATH / name,
            output_path=tmp_path / name / "model",
            input_file_type="jsonschema",
            extra_args=get_current_version_args(
                *("--output-model-type", "pydantic_v2.BaseModel", "--use-exact-imports", "--collapse-root-models"),
                *("--use-title-as-name", "--snake-case-field"),
            ),
            skip_code_validation=False,
        )


@_SKIP_BLACK
def test_openapi_with_refs_exec_current_version(output_file: Path) -> None:
    """Test that OpenAPI schema with $ref generates executable code."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "body_and_parameters.yaml",
        output_path=output_file,
        extra_args=get_current_version_args("--output-model-type", "pydantic_v2.BaseModel"),
        skip_code_validation=False,
        force_exec_validation=True,
    )


@_SKIP_BLACK
def test_openapi_allof_exec_current_version(output_file: Path) -> None:
    """Test that OpenAPI schema with allOf generates executable code."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "allof.yaml",
        output_path=output_file,
        extra_args=get_current_version_args("--output-model-type", "pydantic_v2.BaseModel"),
        skip_code_validation=False,
        force_exec_validation=True,
    )


@_SKIP_BLACK
def test_jsonschema_person_exec_current_version(output_file: Path) -> None:
    """Test that person.json schema generates executable code on current Python."""
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "person.json",
        output_path=output_file,
        extra_args=get_current_version_args("--output-model-type", "pydantic_v2.BaseModel"),
        skip_code_validation=False,
        force_exec_validation=True,
    )


@_SKIP_BLACK
def test_jsonschema_nested_array_exec_current_version(output_file: Path) -> None:
    """Test that nested array JSON Schema generates executable code."""
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "nested_array.json",
        output_path=output_file,
        extra_args=get_current_version_args("--output-model-type", "pydantic_v2.BaseModel"),
        skip_code_validation=False,
        force_exec_validation=True,
    )


@_SKIP_BLACK
def test_jsonschema_circular_reference_exec_current_version(output_file: Path) -> None:
    """Test that circular reference JSON Schema generates executable code."""
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "circular_reference.json",
        output_path=output_file,
        extra_args=get_current_version_args("--output-model-type", "pydantic_v2.BaseModel"),
        skip_code_validation=False,
        force_exec_validation=True,
    )


@pytest.mark.allow_direct_assert
def test_validate_output_files_imports_generated_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test generated single-file outputs are imported during validation."""
    sentinel = tmp_path / "sentinel.txt"
    output_file = tmp_path / "output.py"
    output_file.write_text(
        f"from pathlib import Path\nPath({str(sentinel)!r}).write_text('imported', encoding='utf-8')\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(main_conftest, "_get_concurrent_interpreters_module", lambda: None)

    _validate_output_files(output_file, get_current_version_args(), force_exec_validation=True)

    assert sentinel.read_text(encoding="utf-8") == "imported"


@pytest.mark.allow_direct_assert
def test_validate_output_files_uses_successful_subinterpreter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test generated single-file outputs skip normal import after subinterpreter validation."""
    sentinel = tmp_path / "sentinel.txt"
    output_file = tmp_path / "output.py"
    output_file.write_text(
        f"from pathlib import Path\nPath({str(sentinel)!r}).write_text('imported', encoding='utf-8')\n",
        encoding="utf-8",
    )
    fake_interpreters = _FakeInterpreters()
    monkeypatch.setattr(main_conftest, "_get_concurrent_interpreters_module", lambda: fake_interpreters)

    _validate_output_files(output_file, get_current_version_args(), force_exec_validation=True)

    assert not sentinel.exists()
    assert output_file.name in fake_interpreters.instances[0].executed_code
    assert fake_interpreters.instances[0].closed


@pytest.mark.allow_direct_assert
def test_validate_output_files_imports_generated_package_modules(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test generated package submodules are imported during validation."""
    sentinel = tmp_path / "sentinel.txt"
    output_dir = tmp_path / "model"
    output_dir.mkdir()
    (output_dir / "__init__.py").write_text("", encoding="utf-8")
    (output_dir / "child.py").write_text(
        f"from pathlib import Path\nPath({str(sentinel)!r}).write_text('imported', encoding='utf-8')\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(main_conftest, "_get_concurrent_interpreters_module", lambda: None)

    _validate_output_files(output_dir, get_current_version_args())

    assert sentinel.read_text(encoding="utf-8") == "imported"


@pytest.mark.allow_direct_assert
def test_try_import_generated_output_uses_subinterpreter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test generated output import uses subinterpreters when available."""
    fake_interpreters = _FakeInterpreters()
    output_file = tmp_path / "output.py"
    output_file.write_text("VALUE = 1\n", encoding="utf-8")
    monkeypatch.setattr(main_conftest, "_get_concurrent_interpreters_module", lambda: fake_interpreters)

    assert _try_import_generated_output_in_subinterpreter(output_file)
    assert output_file.name in fake_interpreters.instances[0].executed_code
    assert fake_interpreters.instances[0].closed


@pytest.mark.allow_direct_assert
def test_try_import_generated_output_falls_back_for_unsupported_subinterpreter_extension(
    output_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test subinterpreter-incompatible extensions fall back to normal imports."""
    fake_interpreters = _FakeInterpreters(
        RuntimeError("ImportError: module _pydantic_core does not support loading in subinterpreters")
    )
    monkeypatch.setattr(main_conftest, "_get_concurrent_interpreters_module", lambda: fake_interpreters)

    assert not _try_import_generated_output_in_subinterpreter(output_file)
    assert fake_interpreters.instances[0].closed


@pytest.mark.allow_direct_assert
def test_try_import_generated_output_caches_unsupported_subinterpreter_extension(
    output_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test subinterpreter-incompatible extensions are detected once per worker."""
    fake_interpreters = _FakeInterpreters(
        RuntimeError("ImportError: module _pydantic_core does not support loading in subinterpreter")
    )
    monkeypatch.setattr(main_conftest, "_get_concurrent_interpreters_module", lambda: fake_interpreters)

    assert not _try_import_generated_output_in_subinterpreter(output_file)
    assert not _try_import_generated_output_in_subinterpreter(output_file)
    assert len(fake_interpreters.instances) == 1
    assert fake_interpreters.instances[0].closed


@pytest.mark.allow_direct_assert
def test_try_import_generated_output_reraises_subinterpreter_import_errors(
    output_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test generated import errors are not hidden by the subinterpreter path."""
    fake_interpreters = _FakeInterpreters(RuntimeError("generated import failed"))
    monkeypatch.setattr(main_conftest, "_get_concurrent_interpreters_module", lambda: fake_interpreters)

    with pytest.raises(RuntimeError, match="generated import failed"):
        _try_import_generated_output_in_subinterpreter(output_file)
    assert fake_interpreters.instances[0].closed


@_SKIP_BLACK
@pytest.mark.skipif(
    sys.version_info[:2] <= (3, 10),
    reason="Need runtime > target (3.10) to test force_exec_validation behavior",
)
def test_force_exec_with_different_target_version(output_file: Path) -> None:
    """Test that force_exec_validation runs exec even with different target version.

    This test uses --target-python-version 3.10 but force_exec_validation=True
    to verify that exec still runs on the current Python version.

    Requirements for this test to be meaningful:
    - Runtime must be > 3.10 (so target < runtime, allowing compile to proceed)
    - This ensures force_exec_validation can bypass the version mismatch skip
    """
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "person.json",
        output_path=output_file,
        extra_args=["--target-python-version", "3.10", "--output-model-type", "pydantic_v2.BaseModel"],
        skip_code_validation=False,
        force_exec_validation=True,
    )


@pytest.mark.allow_direct_assert
def test_get_current_version_args_basic() -> None:
    """Test that get_current_version_args returns correct args."""
    args = get_current_version_args()
    assert args == ["--target-python-version", CURRENT_PYTHON_VERSION]


@pytest.mark.allow_direct_assert
def test_get_current_version_args_with_extra() -> None:
    """Test that get_current_version_args includes extra args."""
    args = get_current_version_args("--use-field-description", "--strict")
    assert args == [
        "--target-python-version",
        CURRENT_PYTHON_VERSION,
        "--use-field-description",
        "--strict",
    ]


@pytest.mark.allow_direct_assert
def test_current_python_version_format() -> None:
    """Test that CURRENT_PYTHON_VERSION matches expected format."""
    expected = f"{sys.version_info[0]}.{sys.version_info[1]}"
    assert expected == CURRENT_PYTHON_VERSION
