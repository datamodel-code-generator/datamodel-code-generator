"""Tests for --input-model option."""

from __future__ import annotations

import importlib
import json
import os
import sys
import types
from argparse import Namespace
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from threading import Event
from typing import TYPE_CHECKING, Any, get_args, get_origin, get_type_hints

import black
import pytest
from pydantic import TypeAdapter, ValidationError
from pydantic.version import VERSION as PYDANTIC_VERSION

from datamodel_code_generator import (
    DataModelType,
    GenerateConfig,
    InputFileType,
    PythonVersion,
    arguments,
)
from datamodel_code_generator import __main__ as main_module
from datamodel_code_generator.__main__ import Exit
from datamodel_code_generator.enums import InputModelRefStrategy
from datamodel_code_generator.format import Formatter
from datamodel_code_generator.input_model import load_model_schema
from tests.conftest import (
    BUILTIN_FORMATTER_VALUE,
    TEST_DEFAULT_FORMATTER_ENV,
    assert_inputs_not_mutated,
    assert_output,
    freeze_time,
)
from tests.data.python.input_model import inherited_overrides, union_annotations
from tests.data.python.input_model.inherited_override_runtime import CASES as INHERITED_OVERRIDE_CASES
from tests.data.python.input_model.union_runtime import CASES, VALUES, describe
from tests.main.conftest import (
    DATA_PATH,
    EXPECTED_MAIN_PATH,
    _generated_model,
    _model_json_validator,
    _uses_external_test_default_formatter,
    run_generate_and_assert,
    run_main_and_assert,
    run_main_with_args,
)

if TYPE_CHECKING:
    from collections.abc import Generator, Sequence

EXPECTED_INPUT_MODEL_PATH = Path(__file__).parent / "data" / "expected" / "main" / "input_model"
TIMESTAMP = "1985-10-26T01:21:00-07:00"
_MISSING_SYS_MODULE = object()


def _assert_sys_module_missing(module_name: str) -> None:
    """Assert sys.modules does not contain module_name."""
    __tracebackhide__ = True
    if module_name in sys.modules:  # pragma: no cover
        pytest.fail(f"Expected sys.modules to not contain {module_name!r}")


def _assert_sys_module_is(module_name: str, expected_module: types.ModuleType) -> None:
    """Assert sys.modules contains the expected module object."""
    __tracebackhide__ = True
    actual_module = sys.modules.get(module_name)
    if actual_module is not expected_module:  # pragma: no cover
        pytest.fail(f"Expected sys.modules[{module_name!r}] to be restored")


def _assert_sys_modules_with_prefix(module_prefix: str, expected_modules: set[str]) -> None:
    """Assert sys.modules keys with module_prefix match the expected set."""
    __tracebackhide__ = True
    actual_modules = {module_name for module_name in sys.modules if module_name.startswith(module_prefix)}
    if actual_modules != expected_modules:  # pragma: no cover
        pytest.fail(f"Expected sys.modules keys with prefix {module_prefix!r} to be restored")


@contextmanager
def _without_sys_module(module_name: str) -> Generator[None, None, None]:
    """Temporarily remove a sys.modules entry and restore the previous state."""
    previous_module = sys.modules.pop(module_name, _MISSING_SYS_MODULE)
    try:
        yield
    finally:
        if previous_module is _MISSING_SYS_MODULE:
            sys.modules.pop(module_name, None)
        else:
            sys.modules[module_name] = previous_module


def _input_model_args(
    input_models: str | Sequence[str],
    *,
    output_path: Path | None = None,
    extra_args: Sequence[str] | None = None,
) -> list[str]:
    args: list[str] = []
    match input_models:
        case str():
            args.extend(["--input-model", input_models])
        case _:
            for input_model in input_models:
                args.extend(["--input-model", input_model])
    if output_path is not None:
        args.extend(["--output", str(output_path)])
    if not (extra := list(extra_args or ())):
        return args
    args.extend(extra)
    return args


def run_input_model_and_assert(
    *,
    input_model: str,
    output_path: Path,
    expected_file: Path,
    extra_args: Sequence[str] | None = None,
) -> None:
    """Run main with --input-model and assert results."""
    __tracebackhide__ = True
    with freeze_time(TIMESTAMP):
        run_main_with_args(
            _input_model_args(input_model, output_path=output_path, extra_args=extra_args),
            use_parsed_source_cache=False,
            use_builtin_default_formatter=False,
            isolate_model_template_cache=True,
        )
    assert_output(output_path.read_text(encoding="utf-8"), expected_file)


def run_input_model_error_and_assert(
    *,
    input_model: str,
    extra_args: Sequence[str] | None = None,
    capsys: pytest.CaptureFixture[str],
    expected_stderr_contains: str,
) -> None:
    """Run main with --input-model expecting error and assert stderr."""
    __tracebackhide__ = True
    run_main_with_args(
        _input_model_args(input_model, extra_args=extra_args),
        expected_exit=Exit.ERROR,
        capsys=capsys,
        expected_stderr_contains=expected_stderr_contains,
        use_parsed_source_cache=False,
        use_builtin_default_formatter=False,
        isolate_model_template_cache=True,
    )


@pytest.fixture(autouse=True)
def reset_namespace(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reset the global namespace before each test."""
    new_namespace = Namespace(no_color=False)
    monkeypatch.setattr(arguments, "namespace", new_namespace)
    monkeypatch.setattr(main_module, "namespace", new_namespace)


@pytest.mark.cli_doc(
    options=["--input-model"],
    option_description="""Import a Python type or dict schema from a module or Python file.

Use the format `module:Object` or `path/to/file.py:Object` to specify the type.""",
    cli_args=["--input-model", "mymodule:MyModel"],
    input_model="mymodule:MyModel",
    expected_stdout="",
)
def test_input_model_pydantic_basemodel(tmp_path: Path) -> None:
    """Test Pydantic BaseModel input converts to Pydantic BaseModel output (default)."""
    run_input_model_and_assert(
        input_model="tests.data.python.input_model.pydantic_models:User",
        output_path=tmp_path / "output.py",
        expected_file=EXPECTED_INPUT_MODEL_PATH / "pydantic_basemodel.py",
    )


def test_input_model_pydantic_to_typeddict(tmp_path: Path) -> None:
    """Test generating TypedDict from Pydantic model."""
    run_input_model_and_assert(
        input_model="tests.data.python.input_model.pydantic_models:User",
        output_path=tmp_path / "output.py",
        expected_file=EXPECTED_INPUT_MODEL_PATH / "pydantic_to_typeddict.py",
        extra_args=["--output-model-type", "typing.TypedDict"],
    )


def test_input_model_pydantic_with_jsonschema_type(tmp_path: Path) -> None:
    """Test --input-model with explicit jsonschema input-file-type."""
    run_input_model_and_assert(
        input_model="tests.data.python.input_model.pydantic_models:User",
        output_path=tmp_path / "output.py",
        expected_file=EXPECTED_INPUT_MODEL_PATH / "pydantic_with_jsonschema.py",
        extra_args=["--input-file-type", "jsonschema"],
    )


def test_input_model_pydantic_non_jsonschema_error(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Test error when Pydantic model used with non-jsonschema input-file-type."""
    run_input_model_error_and_assert(
        input_model="tests.data.python.input_model.pydantic_models:User",
        extra_args=["--input-file-type", "openapi", "--output", str(tmp_path / "output.py")],
        capsys=capsys,
        expected_stderr_contains="--input-file-type must be 'jsonschema'",
    )


def test_input_model_dict_with_jsonschema(tmp_path: Path) -> None:
    """Test dict input with --input-file-type jsonschema."""
    from tests.data.python.input_model.dict_schemas import USER_SCHEMA

    with assert_inputs_not_mutated({"USER_SCHEMA": USER_SCHEMA}):
        run_input_model_and_assert(
            input_model="tests.data.python.input_model.dict_schemas:USER_SCHEMA",
            output_path=tmp_path / "output.py",
            expected_file=EXPECTED_INPUT_MODEL_PATH / "dict_with_jsonschema.py",
            extra_args=["--input-file-type", "jsonschema"],
        )


def test_input_model_dict_without_type_error(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Test that dict without --input-file-type raises error."""
    run_input_model_error_and_assert(
        input_model="tests.data.python.input_model.dict_schemas:USER_SCHEMA",
        extra_args=["--output", str(tmp_path / "output.py")],
        capsys=capsys,
        expected_stderr_contains="--input-file-type is required",
    )


def test_input_model_dict_openapi(tmp_path: Path) -> None:
    """Test dict input as OpenAPI spec."""
    run_input_model_and_assert(
        input_model="tests.data.python.input_model.dict_schemas:OPENAPI_SPEC",
        output_path=tmp_path / "output.py",
        expected_file=EXPECTED_INPUT_MODEL_PATH / "dict_openapi.py",
        extra_args=["--input-file-type", "openapi"],
    )


def test_input_model_dict_preserves_json_round_trip(tmp_path: Path) -> None:
    """Keep the historical JSON key coercion for module-owned dict schemas."""
    from tests.data.python.input_model.dict_schemas import JSON_COERCIBLE_SCHEMA

    with assert_inputs_not_mutated({"JSON_COERCIBLE_SCHEMA": JSON_COERCIBLE_SCHEMA}):
        run_input_model_and_assert(
            input_model="tests.data.python.input_model.dict_schemas:JSON_COERCIBLE_SCHEMA",
            output_path=tmp_path / "output.py",
            expected_file=EXPECTED_INPUT_MODEL_PATH / "dict_json_coercible.py",
            extra_args=["--input-file-type", "jsonschema", "--disable-timestamp"],
        )


def test_input_model_dict_rejects_non_json_value(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Reject values that the former CLI JSON boundary could not serialize."""
    run_input_model_error_and_assert(
        input_model="tests.data.python.input_model.dict_schemas:NON_JSON_SCHEMA",
        extra_args=["--input-file-type", "jsonschema", "--output", str(tmp_path / "output.py")],
        capsys=capsys,
        expected_stderr_contains="Object of type set is not JSON serializable",
    )


def test_input_model_std_dataclass(tmp_path: Path) -> None:
    """Test stdlib dataclass input converts to Pydantic BaseModel output (default)."""
    run_input_model_and_assert(
        input_model="tests.data.python.input_model.dataclass_models:User",
        output_path=tmp_path / "output.py",
        expected_file=EXPECTED_INPUT_MODEL_PATH / "std_dataclass.py",
    )


def test_input_model_pydantic_dataclass(tmp_path: Path) -> None:
    """Test Pydantic dataclass input converts to Pydantic BaseModel output (default)."""
    run_input_model_and_assert(
        input_model="tests.data.python.input_model.pydantic_dataclass_models:User",
        output_path=tmp_path / "output.py",
        expected_file=EXPECTED_INPUT_MODEL_PATH / "pydantic_dataclass.py",
    )


def test_input_model_typeddict(tmp_path: Path) -> None:
    """Test TypedDict input converts to Pydantic BaseModel output (default)."""
    run_input_model_and_assert(
        input_model="tests.data.python.input_model.typeddict_models:User",
        output_path=tmp_path / "output.py",
        expected_file=EXPECTED_INPUT_MODEL_PATH / "typeddict.py",
    )


def test_input_model_dataclass_non_jsonschema_error(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Test error when dataclass used with non-jsonschema input-file-type."""
    run_input_model_error_and_assert(
        input_model="tests.data.python.input_model.dataclass_models:User",
        extra_args=["--input-file-type", "openapi", "--output", str(tmp_path / "output.py")],
        capsys=capsys,
        expected_stderr_contains="--input-file-type must be 'jsonschema'",
    )


def test_input_model_invalid_format(capsys: pytest.CaptureFixture[str]) -> None:
    """Test error when colon is missing."""
    run_input_model_error_and_assert(
        input_model="pydantic.BaseModel",
        capsys=capsys,
        expected_stderr_contains="Invalid --input-model format",
    )


def test_input_model_invalid_module(capsys: pytest.CaptureFixture[str]) -> None:
    """Test error when module doesn't exist."""
    run_input_model_error_and_assert(
        input_model="nonexistent_module_12345:Model",
        capsys=capsys,
        expected_stderr_contains="Cannot find module",
    )


def test_input_model_invalid_attr(capsys: pytest.CaptureFixture[str]) -> None:
    """Test error when class doesn't exist in module."""
    run_input_model_error_and_assert(
        input_model="pydantic:NonexistentClass12345",
        capsys=capsys,
        expected_stderr_contains="has no attribute",
    )


def test_input_model_unsupported_type(capsys: pytest.CaptureFixture[str]) -> None:
    """Test error when object is not a supported type."""
    run_input_model_error_and_assert(
        input_model="pathlib:Path",
        capsys=capsys,
        expected_stderr_contains="is not a supported type",
    )


def test_input_model_mutual_exclusion_with_input(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Test --input-model cannot be used with --input."""
    run_input_model_error_and_assert(
        input_model="tests.data.python.input_model.pydantic_models:User",
        extra_args=["--input", str(tmp_path / "schema.json")],
        capsys=capsys,
        expected_stderr_contains="--input-model cannot be used with --input or --url",
    )


def test_input_model_mutual_exclusion_with_url(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Test --input-model cannot be used with --url."""
    run_input_model_error_and_assert(
        input_model="tests.data.python.input_model.pydantic_models:User",
        extra_args=["--url", "https://example.com/schema.json"],
        capsys=capsys,
        expected_stderr_contains="--input-model cannot be used with --input or --url",
    )


def test_input_model_mutual_exclusion_with_watch(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Test --input-model cannot be used with --watch."""
    run_input_model_error_and_assert(
        input_model="tests.data.python.input_model.pydantic_models:User",
        extra_args=["--watch", "--output", str(tmp_path / "output.py")],
        capsys=capsys,
        expected_stderr_contains="--watch cannot be used with --input-model",
    )


def test_input_model_restores_cwd_in_sys_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test that --input-model removes its temporary cwd import path."""
    cwd = str(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert cwd not in sys.path

    original_sys_path = sys.path.copy()
    try:
        run_input_model_and_assert(
            input_model="tests.data.python.input_model.pydantic_models:User",
            output_path=tmp_path / "output.py",
            expected_file=EXPECTED_INPUT_MODEL_PATH / "pydantic_basemodel.py",
        )
        assert sys.path == original_sys_path
    finally:
        sys.path[:] = original_sys_path


def test_input_model_restores_cwd_package_modules(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test same-named cwd packages are loaded independently."""
    from datamodel_code_generator import InputFileType
    from datamodel_code_generator.input_model import Error, load_model_schema

    package_name = "_shadowed_input_package"
    helper_name = "_shadowed_input_helper"
    existing_modules = {name for name in sys.modules if name.startswith(package_name)}
    existing_helper = sys.modules.get(helper_name)
    for directory_name, marker in (("first", "first"), ("second", "second")):
        directory = tmp_path / directory_name
        package = directory / package_name
        package.mkdir(parents=True)
        (package / "__init__.py").write_text("", encoding="utf-8")
        (directory / f"{helper_name}.py").write_text(f"MARKER = {marker!r}\n", encoding="utf-8")
        (package / "model.py").write_text(
            "import importlib\n"
            "from pathlib import Path\n\n"
            "from pydantic import BaseModel\n\n"
            f"helper = importlib.import_module({helper_name!r})\n"
            "Path(__file__).parents[1].joinpath('loaded.txt').write_text(helper.MARKER, encoding='utf-8')\n\n"
            "class User(BaseModel):\n"
            "    name: str\n"
            "    age: int\n",
            encoding="utf-8",
        )

    monkeypatch.chdir(tmp_path / "first")
    with pytest.raises(Error):
        load_model_schema([f"{package_name}.model:Missing"], InputFileType.JsonSchema)
    _assert_sys_modules_with_prefix(package_name, existing_modules)
    _assert_sys_module_is(helper_name, existing_helper)

    for directory_name, marker in (("first", "first"), ("second", "second")):
        directory = tmp_path / directory_name
        monkeypatch.chdir(directory)
        run_input_model_and_assert(
            input_model=f"{package_name}.model:User",
            output_path=directory / "output.py",
            expected_file=EXPECTED_INPUT_MODEL_PATH / "pydantic_basemodel.py",
        )
        assert (directory / "loaded.txt").read_text(encoding="utf-8") == marker
        _assert_sys_modules_with_prefix(package_name, existing_modules)
        _assert_sys_module_is(helper_name, existing_helper)


def test_input_model_preserves_unrelated_concurrent_import(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Do not remove cwd modules imported concurrently by another thread."""
    from datamodel_code_generator import InputFileType
    from datamodel_code_generator.input_model import load_model_schema

    control_name = "_input_model_import_control"
    input_module_name = "_tracked_input_model"
    unrelated_module_name = "_unrelated_input_model_import"
    input_entered = Event()
    release_input = Event()
    unrelated_entered = Event()
    release_unrelated = Event()
    control = types.ModuleType(control_name)
    control.input_entered = input_entered
    control.release_input = release_input
    control.unrelated_entered = unrelated_entered
    control.release_unrelated = release_unrelated
    monkeypatch.setitem(sys.modules, control_name, control)
    monkeypatch.chdir(tmp_path)
    (tmp_path / f"{input_module_name}.py").write_text(
        f"from {control_name} import input_entered, release_input\n\n"
        "input_entered.set()\n"
        "release_input.wait(5)\n\n"
        "from pydantic import BaseModel\n\n"
        "class User(BaseModel):\n"
        "    name: str\n",
        encoding="utf-8",
    )
    unrelated_directory = tmp_path.parent / f"{tmp_path.name}_unrelated"
    unrelated_directory.mkdir()
    unrelated_path = unrelated_directory / f"{unrelated_module_name}.py"
    unrelated_path.write_text(
        f"from {control_name} import release_unrelated, unrelated_entered\n\n"
        "unrelated_entered.set()\n"
        "release_unrelated.wait(5)\n"
        "VALUE = 'unrelated'\n",
        encoding="utf-8",
    )

    def import_unrelated_module() -> types.ModuleType:
        spec = importlib.util.spec_from_file_location(unrelated_module_name, unrelated_path)
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[unrelated_module_name] = module
        spec.loader.exec_module(module)
        return module

    with _without_sys_module(input_module_name), _without_sys_module(unrelated_module_name):
        try:
            with ThreadPoolExecutor(max_workers=2) as executor:
                input_future = executor.submit(
                    load_model_schema,
                    [f"{input_module_name}:User"],
                    InputFileType.JsonSchema,
                )
                assert input_entered.wait(timeout=5)
                unrelated_future = executor.submit(import_unrelated_module)
                assert unrelated_entered.wait(timeout=5)
                release_input.set()
                schema = input_future.result(timeout=5)
                release_unrelated.set()
                unrelated_module = unrelated_future.result(timeout=5)
        finally:
            release_input.set()
            release_unrelated.set()

        assert schema["title"] == "User"
        assert unrelated_module.VALUE == "unrelated"
        _assert_sys_module_missing(input_module_name)
        _assert_sys_module_is(unrelated_module_name, unrelated_module)


def test_input_model_does_not_reuse_another_loads_temporary_module(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep dotted loading isolated from a concurrent path module with the same name."""
    from datamodel_code_generator import InputFileType
    from datamodel_code_generator.input_model import load_model_schema

    control_name = "_input_model_temporary_control"
    module_name = "_input_model_temporary_collision"
    first_entered = Event()
    release_first = Event()
    second_started = Event()
    second_finished = Event()
    control = types.ModuleType(control_name)
    control.first_entered = first_entered
    control.release_first = release_first
    monkeypatch.setitem(sys.modules, control_name, control)

    first_directory = tmp_path / "first"
    second_directory = tmp_path / "second"
    first_directory.mkdir()
    second_directory.mkdir()
    first_path = first_directory / f"{module_name}.py"
    first_path.write_text(
        "from pydantic import BaseModel\n"
        f"from {control_name} import first_entered, release_first\n\n"
        "class Model(BaseModel):\n"
        "    first: str\n\n"
        "first_entered.set()\n"
        "release_first.wait(5)\n",
        encoding="utf-8",
    )
    (second_directory / f"{module_name}.py").write_text(
        "from pydantic import BaseModel\n\nclass Model(BaseModel):\n    second: int\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(second_directory)

    def load_second() -> dict[str, object]:
        second_started.set()
        try:
            return load_model_schema([f"{module_name}:Model"], InputFileType.JsonSchema)
        finally:
            second_finished.set()

    with _without_sys_module(module_name):
        try:
            with ThreadPoolExecutor(max_workers=2) as executor:
                first_future = executor.submit(
                    load_model_schema,
                    [f"{first_path}:Model"],
                    InputFileType.JsonSchema,
                )
                assert first_entered.wait(timeout=5)
                second_future = executor.submit(load_second)
                assert second_started.wait(timeout=5)
                assert not second_finished.wait(timeout=0.1)
                release_first.set()
                first_schema = first_future.result(timeout=5)
                second_schema = second_future.result(timeout=5)
        finally:
            release_first.set()

    assert "first" in first_schema["properties"]
    assert "second" in second_schema["properties"]
    _assert_sys_module_missing(module_name)


def test_input_model_path_format(tmp_path: Path) -> None:
    """Test --input-model with path format (path/to/file.py:Object)."""
    run_input_model_and_assert(
        input_model="tests/data/python/input_model/pydantic_models.py:User",
        output_path=tmp_path / "output.py",
        expected_file=EXPECTED_INPUT_MODEL_PATH / "path_format.py",
    )


def test_input_model_path_format_restores_sys_modules(tmp_path: Path) -> None:
    """Test path-based --input-model does not keep temporary modules alive."""
    model_path = tmp_path / "temporary_input_model.py"
    model_path.write_text(
        "from pydantic import BaseModel\n\nclass User(BaseModel):\n    name: str\n    age: int\n",
        encoding="utf-8",
    )
    module_name = model_path.stem

    with _without_sys_module(module_name):
        run_input_model_and_assert(
            input_model=f"{model_path}:User",
            output_path=tmp_path / "output.py",
            expected_file=EXPECTED_INPUT_MODEL_PATH / "path_format.py",
        )

        _assert_sys_module_missing(module_name)


def test_input_model_path_format_restores_existing_sys_modules(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test path-based --input-model restores a pre-existing sys.modules entry."""
    model_path = tmp_path / "existing_input_model.py"
    model_path.write_text(
        "from pydantic import BaseModel\n\nclass User(BaseModel):\n    name: str\n    age: int\n",
        encoding="utf-8",
    )
    module_name = model_path.stem
    existing_module = types.ModuleType(module_name)
    monkeypatch.setitem(sys.modules, module_name, existing_module)

    run_input_model_and_assert(
        input_model=f"{model_path}:User",
        output_path=tmp_path / "output.py",
        expected_file=EXPECTED_INPUT_MODEL_PATH / "path_format.py",
    )

    _assert_sys_module_is(module_name, existing_module)


def test_without_sys_module_restores_existing_module() -> None:
    """Test _without_sys_module restores the previous sys.modules entry."""
    module_name = "temporary_existing_input_model"
    existing_module = types.ModuleType(module_name)

    with _without_sys_module(module_name):
        sys.modules[module_name] = existing_module
        with _without_sys_module(module_name):
            _assert_sys_module_missing(module_name)

        _assert_sys_module_is(module_name, existing_module)

    _assert_sys_module_missing(module_name)


def test_input_model_state_cleanup_defensive_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep cleanup safe when paths disappear or modules change concurrently."""
    from datamodel_code_generator.input_model import (
        _module_is_from_directory,
        _path_is_within,
        _remove_input_model_path,
        _remove_local_module,
    )

    module = types.ModuleType("_input_model_cleanup_race")
    _remove_local_module(module.__name__, module)
    assert not _module_is_from_directory(object(), tmp_path)
    namespace_module = types.ModuleType("_input_model_namespace")
    namespace_module.__path__ = [str(tmp_path)]
    assert _module_is_from_directory(namespace_module, tmp_path)
    environment_directory = tmp_path / ".venv"
    environment_module = types.ModuleType("_input_model_environment")
    environment_module.__file__ = str(environment_directory / "site-packages" / "dependency.py")
    assert not _module_is_from_directory(environment_module, tmp_path, environment_directory)

    missing_path_entry = str(tmp_path / "already-removed")
    _remove_input_model_path(missing_path_entry)
    assert missing_path_entry not in sys.path

    def raise_os_error(_path: Path) -> Path:
        raise OSError

    monkeypatch.setattr(Path, "resolve", raise_os_error)
    assert not _path_is_within(tmp_path / "missing.py", tmp_path)


def test_input_model_path_format_filename_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test --input-model with filename.py format (no slash)."""
    from pathlib import Path as PathLib

    monkeypatch.chdir(PathLib("tests/data/python/input_model"))
    run_input_model_and_assert(
        input_model="pydantic_models.py:User",
        output_path=tmp_path / "output.py",
        expected_file=EXPECTED_INPUT_MODEL_PATH / "path_format_filename_only.py",
    )


def test_input_model_path_file_not_found(capsys: pytest.CaptureFixture[str]) -> None:
    """Test error when file path doesn't exist."""
    run_input_model_error_and_assert(
        input_model="./nonexistent_file_12345.py:Model",
        capsys=capsys,
        expected_stderr_contains="File not found",
    )


def test_input_model_path_cannot_load(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test error when spec_from_file_location returns None."""
    import importlib.util

    test_file = tmp_path / "test_model.py"
    test_file.write_text("class Model: pass")

    monkeypatch.setattr(importlib.util, "spec_from_file_location", lambda *_a, **_kw: None)

    run_input_model_error_and_assert(
        input_model=f"{test_file}:Model",
        capsys=capsys,
        expected_stderr_contains="Cannot load module",
    )


def test_input_model_module_import_error(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test error when module import fails."""
    import importlib
    import importlib.util

    class FakeSpec:
        name = "fake_module"

    def fake_find_spec(_name: str) -> FakeSpec:
        return FakeSpec()

    def fake_import_module(_name: str) -> None:
        msg = "fake import error"
        raise ImportError(msg)

    monkeypatch.setattr(importlib.util, "find_spec", fake_find_spec)
    monkeypatch.setattr(importlib, "import_module", fake_import_module)

    run_input_model_error_and_assert(
        input_model="some_module:Model",
        capsys=capsys,
        expected_stderr_contains="Cannot import module",
    )


def test_path_module_name_keeps_same_file_module(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test path module naming keeps the stem when the loaded module is the same file."""
    from datamodel_code_generator.input_model import _get_path_module_name

    model_path = (tmp_path / "same_file_model.py").resolve()
    model_path.write_text("class Model: pass\n", encoding="utf-8")
    module_name = model_path.stem
    existing_module = types.ModuleType(module_name)
    existing_module.__file__ = str(model_path)
    monkeypatch.setitem(sys.modules, module_name, existing_module)

    assert _get_path_module_name(model_path) == module_name


def test_path_module_name_falls_back_when_existing_file_cannot_resolve(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test path module naming keeps the stem when an existing module file cannot resolve."""
    from datamodel_code_generator.input_model import _get_path_module_name

    model_path = (tmp_path / "unresolvable_existing_model.py").resolve()
    model_path.write_text("class Model: pass\n", encoding="utf-8")
    module_name = model_path.stem
    existing_module = types.ModuleType(module_name)
    existing_module.__file__ = "__unresolvable_existing_model__"
    monkeypatch.setitem(sys.modules, module_name, existing_module)

    def fake_resolve(_path: Path, *_args: object, **_kwargs: object) -> Path:
        msg = "cannot resolve"
        raise OSError(msg)

    monkeypatch.setattr(Path, "resolve", fake_resolve)

    assert _get_path_module_name(model_path) == module_name


def test_load_module_from_path_restores_sys_modules_on_exec_error(tmp_path: Path) -> None:
    """Test path module loading restores sys.modules when module execution fails."""
    from datamodel_code_generator.input_model import _load_module_from_path

    model_path = (tmp_path / "failing_input_model.py").resolve()
    model_path.write_text("raise RuntimeError('module failed')\n", encoding="utf-8")
    module_name = model_path.stem

    with _without_sys_module(module_name):
        with pytest.raises(RuntimeError, match="module failed"):
            _load_module_from_path(model_path, str(model_path))

        _assert_sys_module_missing(module_name)


def test_is_input_model_base_schema_requires_dict() -> None:
    """Test base schema detection returns false for non-dict values."""
    from datamodel_code_generator.input_model import _is_input_model_base_schema

    assert not _is_input_model_base_schema("not a schema")


# ============================================================================
# x-python-type preservation tests
# ============================================================================


@pytest.mark.parametrize(
    "test_id",
    [
        "set_type",
        "frozenset_type",
        "mapping_type",
        "sequence_type",
        "nested_model_types",
    ],
)
def test_input_model_preserves_python_types(tmp_path: Path, test_id: str) -> None:
    """Test that Python collection types are preserved when converting Pydantic model."""
    del test_id
    run_input_model_and_assert(
        input_model="tests.data.python.input_model.pydantic_models:ModelWithPythonTypes",
        output_path=tmp_path / "output.py",
        expected_file=EXPECTED_INPUT_MODEL_PATH / "model_with_python_types.py",
    )


@pytest.mark.parametrize(
    ("output_model_type", "expected_file"),
    [
        ("typing.TypedDict", "model_with_python_types_typeddict.py"),
        ("dataclasses.dataclass", "model_with_python_types_dataclass.py"),
    ],
)
def test_input_model_x_python_type_output_formats(tmp_path: Path, output_model_type: str, expected_file: str) -> None:
    """Test that x-python-type works with different output model types."""
    run_input_model_and_assert(
        input_model="tests.data.python.input_model.pydantic_models:ModelWithPythonTypes",
        output_path=tmp_path / "output.py",
        expected_file=EXPECTED_INPUT_MODEL_PATH / expected_file,
        extra_args=["--output-model-type", output_model_type],
    )


def test_input_model_dataclass_with_python_types(tmp_path: Path) -> None:
    """Test that Set/Mapping types are preserved from dataclass input."""
    run_input_model_and_assert(
        input_model="tests.data.python.input_model.dataclass_models:DataclassWithPythonTypes",
        output_path=tmp_path / "output.py",
        expected_file=EXPECTED_INPUT_MODEL_PATH / "dataclass_with_python_types.py",
    )


def test_input_model_recursive_model_types(tmp_path: Path) -> None:
    """Test that recursive models handle x-python-type correctly."""
    run_input_model_and_assert(
        input_model="tests.data.python.input_model.pydantic_models:RecursiveNode",
        output_path=tmp_path / "output.py",
        expected_file=EXPECTED_INPUT_MODEL_PATH / "recursive_model_types.py",
    )


def test_input_model_structured_runtime_annotations(tmp_path: Path) -> None:
    """Preserve runtime Callable structure through external fixtures."""
    run_input_model_and_assert(
        input_model="tests.data.python.input_model.structured_annotations:StructuredAnnotations",
        output_path=tmp_path / "output.py",
        expected_file=EXPECTED_INPUT_MODEL_PATH / "structured_annotations.py",
        extra_args=["--disable-timestamp"],
    )


def test_input_model_transport_preserves_inheritance(tmp_path: Path) -> None:
    """Transport inherited and array-nested runtime expressions without reparsing."""
    run_input_model_and_assert(
        input_model="tests.data.python.input_model.structured_annotations:StructuredChild",
        output_path=tmp_path / "output.py",
        expected_file=EXPECTED_INPUT_MODEL_PATH / "structured_inheritance.py",
        extra_args=["--disable-timestamp"],
    )


def test_input_model_transport_preserves_nested_runtime_identity(tmp_path: Path) -> None:
    """Import the runtime module without treating an outer class as a module."""
    run_input_model_and_assert(
        input_model="tests.data.python.input_model.structured_annotations:NestedRuntimeAnnotations",
        output_path=tmp_path / "output.py",
        expected_file=EXPECTED_INPUT_MODEL_PATH / "nested_runtime_annotations.py",
        extra_args=["--formatters", "builtin", "--disable-timestamp"],
    )


@pytest.mark.parametrize(
    "test_id",
    [
        "optional_set",
        "union_none_frozenset",
        "optional_mapping_union_syntax",
    ],
)
def test_input_model_optional_types(tmp_path: Path, test_id: str) -> None:
    """Test that optional/union Python types are preserved when converting Pydantic model."""
    del test_id
    run_input_model_and_assert(
        input_model="tests.data.python.input_model.pydantic_models:ModelWithPythonTypes",
        output_path=tmp_path / "output.py",
        expected_file=EXPECTED_INPUT_MODEL_PATH / "model_with_python_types.py",
    )


# ============================================================================
# Callable and unserializable type tests
# ============================================================================


@pytest.mark.parametrize(
    "test_id",
    [
        "basic",
        "multi_param",
        "variadic",
        "no_param",
        "optional",
        "type_field",
        "nested",
    ],
)
def test_input_model_callable_types(tmp_path: Path, test_id: str) -> None:
    """Test that Callable and Type annotations are preserved when converting Pydantic model."""
    del test_id
    run_input_model_and_assert(
        input_model="tests.data.python.input_model.pydantic_models:ModelWithCallableTypes",
        output_path=tmp_path / "output.py",
        expected_file=EXPECTED_INPUT_MODEL_PATH / "model_with_callable_types.py",
    )


def test_input_model_nested_model_with_callable(tmp_path: Path) -> None:
    """Test that nested models with Callable types in $defs are processed."""
    run_input_model_and_assert(
        input_model="tests.data.python.input_model.pydantic_models:ModelWithNestedCallable",
        output_path=tmp_path / "output.py",
        expected_file=EXPECTED_INPUT_MODEL_PATH / "nested_model_with_callable.py",
    )


def test_input_model_custom_class(tmp_path: Path) -> None:
    """Test that custom classes trigger handle_invalid_for_json_schema."""
    run_input_model_and_assert(
        input_model="tests.data.python.input_model.pydantic_models:ModelWithCustomClass",
        output_path=tmp_path / "output.py",
        expected_file=EXPECTED_INPUT_MODEL_PATH / "custom_class.py",
    )


def test_input_model_union_callable(tmp_path: Path) -> None:
    """Test that Union[Callable, int] and raw Callable are preserved."""
    run_input_model_and_assert(
        input_model="tests.data.python.input_model.pydantic_models:ModelWithUnionCallable",
        output_path=tmp_path / "output.py",
        expected_file=EXPECTED_INPUT_MODEL_PATH / "union_callable.py",
    )


def test_input_model_custom_generic_type_import(tmp_path: Path) -> None:
    """Test that custom generic types are properly imported with full module path."""
    run_input_model_and_assert(
        input_model="tests.data.python.input_model.pydantic_models:ModelWithCustomGeneric",
        output_path=tmp_path / "output.py",
        expected_file=EXPECTED_INPUT_MODEL_PATH / "custom_generic_type_import.py",
    )


def test_input_model_default_put_dict_import(tmp_path: Path) -> None:
    """Test that DefaultPutDict generic type is properly imported from parser module."""
    run_input_model_and_assert(
        input_model="tests.data.python.input_model.pydantic_models:ModelWithDefaultPutDict",
        output_path=tmp_path / "output.py",
        expected_file=EXPECTED_INPUT_MODEL_PATH / "default_put_dict_import.py",
    )


# ============================================================================
# --input-model-ref-strategy tests
# ============================================================================


@pytest.mark.cli_doc(
    options=["--input-model-ref-strategy"],
    option_description="""Strategy for referenced types when using --input-model.

The `--input-model-ref-strategy` option determines whether to regenerate or import
referenced types. Use `regenerate-all` (default) to regenerate all types,
`reuse-foreign` to import types from different families (like enums when generating
dataclasses) while regenerating same-family types, or `reuse-all` to import all
referenced types directly.""",
    cli_args=["--input-model-ref-strategy", "reuse-foreign"],
    input_model="mymodule:MyModel",
    expected_stdout="",
)
def test_input_model_ref_strategy_regenerate_all_default(tmp_path: Path) -> None:
    """Test default regenerate-all strategy regenerates all types."""
    run_input_model_and_assert(
        input_model="tests.data.python.input_model.nested_models:User",
        output_path=tmp_path / "output.py",
        expected_file=EXPECTED_INPUT_MODEL_PATH / "ref_strategy_regenerate_all.py",
        extra_args=["--output-model-type", "typing.TypedDict"],
    )


def test_input_model_ref_strategy_regenerate_all_explicit(tmp_path: Path) -> None:
    """Test explicit regenerate-all strategy regenerates all types."""
    run_input_model_and_assert(
        input_model="tests.data.python.input_model.nested_models:User",
        output_path=tmp_path / "output.py",
        expected_file=EXPECTED_INPUT_MODEL_PATH / "ref_strategy_regenerate_all.py",
        extra_args=[
            "--output-model-type",
            "typing.TypedDict",
            "--input-model-ref-strategy",
            "regenerate-all",
        ],
    )


def test_input_model_ref_strategy_reuse_foreign(tmp_path: Path) -> None:
    """Test reuse-foreign imports enum (always) and same-family types."""
    run_input_model_and_assert(
        input_model="tests.data.python.input_model.nested_models:User",
        output_path=tmp_path / "output.py",
        expected_file=EXPECTED_INPUT_MODEL_PATH / "ref_strategy_reuse_foreign.py",
        extra_args=[
            "--output-model-type",
            "typing.TypedDict",
            "--input-model-ref-strategy",
            "reuse-foreign",
        ],
    )


def test_input_model_ref_strategy_reuse_foreign_no_regeneration(tmp_path: Path) -> None:
    """Test reuse-foreign imports only types compatible with output (enum always, same family)."""
    run_input_model_and_assert(
        input_model="tests.data.python.input_model.nested_models:User",
        output_path=tmp_path / "output.py",
        expected_file=EXPECTED_INPUT_MODEL_PATH / "ref_strategy_reuse_foreign.py",
        extra_args=[
            "--output-model-type",
            "typing.TypedDict",
            "--input-model-ref-strategy",
            "reuse-foreign",
        ],
    )


def test_input_model_ref_strategy_reuse_all(tmp_path: Path) -> None:
    """Test reuse-all strategy imports all referenced types."""
    run_input_model_and_assert(
        input_model="tests.data.python.input_model.nested_models:User",
        output_path=tmp_path / "output.py",
        expected_file=EXPECTED_INPUT_MODEL_PATH / "ref_strategy_reuse_all.py",
        extra_args=[
            "--output-model-type",
            "typing.TypedDict",
            "--input-model-ref-strategy",
            "reuse-all",
        ],
    )


def test_input_model_path_ref_strategy_reuse_all_keeps_stem_imports(tmp_path: Path) -> None:
    """Test path-based reuse-all keeps the existing stem-based import path."""
    run_input_model_and_assert(
        input_model="tests/data/python/input_model/nested_models.py:User",
        output_path=tmp_path / "output.py",
        expected_file=EXPECTED_INPUT_MODEL_PATH / "path_ref_strategy_reuse_all.py",
        extra_args=[
            "--output-model-type",
            "typing.TypedDict",
            "--input-model-ref-strategy",
            "reuse-all",
        ],
    )


def test_input_model_ref_strategy_reuse_all_no_regeneration(tmp_path: Path) -> None:
    """Test reuse-all strategy does not regenerate any referenced classes."""
    run_input_model_and_assert(
        input_model="tests.data.python.input_model.nested_models:User",
        output_path=tmp_path / "output.py",
        expected_file=EXPECTED_INPUT_MODEL_PATH / "ref_strategy_reuse_all.py",
        extra_args=[
            "--output-model-type",
            "typing.TypedDict",
            "--input-model-ref-strategy",
            "reuse-all",
        ],
    )


def test_input_model_ref_strategy_without_input_model(tmp_path: Path) -> None:
    """Test that --input-model-ref-strategy is ignored without --input-model."""
    schema_file = tmp_path / "schema.json"
    schema_file.write_text('{"type": "object", "properties": {"name": {"type": "string"}}}')

    args = [
        "--input",
        str(schema_file),
        "--output",
        str(tmp_path / "output.py"),
        "--input-model-ref-strategy",
        "reuse-all",
    ]
    run_main_with_args(
        args,
        use_parsed_source_cache=False,
        use_builtin_default_formatter=False,
        isolate_model_template_cache=True,
    )


def test_input_model_ref_strategy_no_nested_types(tmp_path: Path) -> None:
    """Test reuse-all strategy with Pydantic model that has no nested types (no $defs)."""
    run_input_model_and_assert(
        input_model="tests.data.python.input_model.pydantic_models:User",
        output_path=tmp_path / "output.py",
        expected_file=EXPECTED_INPUT_MODEL_PATH / "ref_strategy_no_nested_types.py",
        extra_args=[
            "--output-model-type",
            "dataclasses.dataclass",
            "--input-model-ref-strategy",
            "reuse-all",
        ],
    )


def test_input_model_ref_strategy_dataclass_reuse_foreign(tmp_path: Path) -> None:
    """Test reuse-foreign strategy with dataclass input."""
    run_input_model_and_assert(
        input_model="tests.data.python.input_model.dataclass_nested:Task",
        output_path=tmp_path / "output.py",
        expected_file=EXPECTED_INPUT_MODEL_PATH / "ref_strategy_dataclass_reuse_foreign.py",
        extra_args=[
            "--output-model-type",
            "typing.TypedDict",
            "--input-model-ref-strategy",
            "reuse-foreign",
        ],
    )


def test_input_model_ref_strategy_typeddict_reuse_all(tmp_path: Path) -> None:
    """Test reuse-all strategy with TypedDict input."""
    run_input_model_and_assert(
        input_model="tests.data.python.input_model.typeddict_nested:Member",
        output_path=tmp_path / "output.py",
        expected_file=EXPECTED_INPUT_MODEL_PATH / "ref_strategy_typeddict_reuse_all.py",
        extra_args=[
            "--output-model-type",
            "dataclasses.dataclass",
            "--input-model-ref-strategy",
            "reuse-all",
        ],
    )


def test_input_model_ref_strategy_typeddict_reuse_foreign(tmp_path: Path) -> None:
    """Test reuse-foreign strategy with TypedDict input imports enum, regenerates typeddict."""
    run_input_model_and_assert(
        input_model="tests.data.python.input_model.typeddict_nested:Member",
        output_path=tmp_path / "output.py",
        expected_file=EXPECTED_INPUT_MODEL_PATH / "ref_strategy_typeddict_reuse_foreign.py",
        extra_args=[
            "--output-model-type",
            "dataclasses.dataclass",
            "--input-model-ref-strategy",
            "reuse-foreign",
        ],
    )


def test_input_model_ref_strategy_reuse_foreign_same_family_typeddict(tmp_path: Path) -> None:
    """Test reuse-foreign imports TypedDict when output is TypedDict (same family)."""
    run_input_model_and_assert(
        input_model="tests.data.python.input_model.mixed_nested:ModelWithTypedDict",
        output_path=tmp_path / "output.py",
        expected_file=EXPECTED_INPUT_MODEL_PATH / "ref_strategy_reuse_foreign_same_family_typeddict.py",
        extra_args=[
            "--output-model-type",
            "typing.TypedDict",
            "--input-model-ref-strategy",
            "reuse-foreign",
        ],
    )


def test_input_model_ref_strategy_reuse_foreign_different_family_regenerate(tmp_path: Path) -> None:
    """Test reuse-foreign regenerates Pydantic model when output is TypedDict."""
    run_input_model_and_assert(
        input_model="tests.data.python.input_model.mixed_nested:ModelWithPydantic",
        output_path=tmp_path / "output.py",
        expected_file=EXPECTED_INPUT_MODEL_PATH / "ref_strategy_reuse_foreign_different_family.py",
        extra_args=[
            "--output-model-type",
            "typing.TypedDict",
            "--input-model-ref-strategy",
            "reuse-foreign",
        ],
    )


def test_input_model_ref_strategy_reuse_foreign_same_family_dataclass(tmp_path: Path) -> None:
    """Test reuse-foreign imports dataclass when output is dataclass (same family)."""
    run_input_model_and_assert(
        input_model="tests.data.python.input_model.mixed_nested:ModelWithDataclass",
        output_path=tmp_path / "output.py",
        expected_file=EXPECTED_INPUT_MODEL_PATH / "ref_strategy_reuse_foreign_same_family_dataclass.py",
        extra_args=[
            "--output-model-type",
            "dataclasses.dataclass",
            "--input-model-ref-strategy",
            "reuse-foreign",
        ],
    )


def test_input_model_ref_strategy_reuse_foreign_mixed_types(tmp_path: Path) -> None:
    """Test reuse-foreign with mixed nested types (TypedDict, Pydantic, dataclass)."""
    run_input_model_and_assert(
        input_model="tests.data.python.input_model.mixed_nested:ModelWithMixed",
        output_path=tmp_path / "output.py",
        expected_file=EXPECTED_INPUT_MODEL_PATH / "ref_strategy_reuse_foreign_mixed_types.py",
        extra_args=[
            "--output-model-type",
            "typing.TypedDict",
            "--input-model-ref-strategy",
            "reuse-foreign",
        ],
    )


def test_input_model_ref_strategy_reuse_foreign_pydantic_output(tmp_path: Path) -> None:
    """Test reuse-foreign imports Pydantic when output is Pydantic (same family)."""
    run_input_model_and_assert(
        input_model="tests.data.python.input_model.mixed_nested:ModelWithPydantic",
        output_path=tmp_path / "output.py",
        expected_file=EXPECTED_INPUT_MODEL_PATH / "ref_strategy_reuse_foreign_pydantic_output.py",
        extra_args=[
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--input-model-ref-strategy",
            "reuse-foreign",
        ],
    )


def test_input_model_ref_strategy_reuse_foreign_msgspec_output(tmp_path: Path) -> None:
    """Test reuse-foreign regenerates non-msgspec types when output is msgspec."""
    run_input_model_and_assert(
        input_model="tests.data.python.input_model.mixed_nested:ModelWithPydantic",
        output_path=tmp_path / "output.py",
        expected_file=EXPECTED_INPUT_MODEL_PATH / "ref_strategy_reuse_foreign_msgspec_output.py",
        extra_args=[
            "--output-model-type",
            "msgspec.Struct",
            "--input-model-ref-strategy",
            "reuse-foreign",
        ],
    )


def test_input_model_config_class(tmp_path: Path) -> None:
    """Test that config classes like GenerateConfig are properly handled."""
    run_input_model_and_assert(
        input_model="datamodel_code_generator.config:GenerateConfig",
        output_path=tmp_path / "output.py",
        expected_file=EXPECTED_INPUT_MODEL_PATH / "config_class.py",
        extra_args=["--output-model-type", "typing.TypedDict"],
    )


# ============================================================================
# Inheritance support tests (single and multiple --input-model)
# ============================================================================


def run_multiple_input_models_and_assert(
    *,
    input_models: Sequence[str],
    output_path: Path,
    expected_file: Path,
    extra_args: Sequence[str] | None = None,
) -> None:
    """Run main with multiple --input-model and assert results."""
    __tracebackhide__ = True
    with freeze_time(TIMESTAMP):
        run_main_with_args(
            _input_model_args(input_models, output_path=output_path, extra_args=extra_args),
            use_parsed_source_cache=False,
            use_builtin_default_formatter=False,
            isolate_model_template_cache=True,
        )
    assert_output(output_path.read_text(encoding="utf-8"), expected_file)


def run_multiple_input_models_error_and_assert(
    *,
    input_models: Sequence[str],
    extra_args: Sequence[str] | None = None,
    capsys: pytest.CaptureFixture[str],
    expected_stderr_contains: str,
) -> None:
    """Run main with multiple --input-model expecting error and assert stderr."""
    __tracebackhide__ = True
    run_main_with_args(
        _input_model_args(input_models, extra_args=extra_args),
        expected_exit=Exit.ERROR,
        capsys=capsys,
        expected_stderr_contains=expected_stderr_contains,
        use_parsed_source_cache=False,
        use_builtin_default_formatter=False,
        isolate_model_template_cache=True,
    )


def test_input_model_single_with_inheritance(tmp_path: Path) -> None:
    """Test single --input-model with inherited model generates inheritance chain."""
    run_input_model_and_assert(
        input_model="tests.data.python.input_model.inheritance_models:ChildA",
        output_path=tmp_path / "output.py",
        expected_file=EXPECTED_INPUT_MODEL_PATH / "single_inheritance.py",
        extra_args=["--output-model-type", "typing.TypedDict"],
    )


def test_input_model_single_multi_level_inheritance(tmp_path: Path) -> None:
    """Test single --input-model with multi-level inheritance."""
    run_input_model_and_assert(
        input_model="tests.data.python.input_model.inheritance_models:GrandChild",
        output_path=tmp_path / "output.py",
        expected_file=EXPECTED_INPUT_MODEL_PATH / "multi_level_inheritance.py",
        extra_args=["--output-model-type", "typing.TypedDict"],
    )


def test_input_model_single_no_inheritance(tmp_path: Path) -> None:
    """Test single --input-model with model that has no inheritance."""
    run_input_model_and_assert(
        input_model="tests.data.python.input_model.inheritance_models:NoInheritance",
        output_path=tmp_path / "output.py",
        expected_file=EXPECTED_INPUT_MODEL_PATH / "no_inheritance.py",
        extra_args=["--output-model-type", "typing.TypedDict"],
    )


def test_input_model_multiple_forked_inheritance(tmp_path: Path) -> None:
    """Test multiple --input-model with forked inheritance shares common parent."""
    run_multiple_input_models_and_assert(
        input_models=[
            "tests.data.python.input_model.inheritance_models:ChildA",
            "tests.data.python.input_model.inheritance_models:ChildB",
        ],
        output_path=tmp_path / "output.py",
        expected_file=EXPECTED_INPUT_MODEL_PATH / "forked_inheritance.py",
        extra_args=["--output-model-type", "typing.TypedDict"],
    )


def test_input_model_multiple_mixed_inheritance(tmp_path: Path) -> None:
    """Test multiple --input-model with different inheritance depths."""
    run_multiple_input_models_and_assert(
        input_models=[
            "tests.data.python.input_model.inheritance_models:ChildA",
            "tests.data.python.input_model.inheritance_models:GrandChild",
        ],
        output_path=tmp_path / "output.py",
        expected_file=EXPECTED_INPUT_MODEL_PATH / "mixed_inheritance.py",
        extra_args=["--output-model-type", "typing.TypedDict"],
    )


def test_input_model_multiple_with_pydantic_output(tmp_path: Path) -> None:
    """Test multiple --input-model works with Pydantic output."""
    run_multiple_input_models_and_assert(
        input_models=[
            "tests.data.python.input_model.inheritance_models:ChildA",
            "tests.data.python.input_model.inheritance_models:ChildB",
        ],
        output_path=tmp_path / "output.py",
        expected_file=EXPECTED_INPUT_MODEL_PATH / "multiple_with_pydantic_output.py",
        extra_args=["--output-model-type", "pydantic_v2.BaseModel"],
    )


def test_input_model_multiple_with_dataclass_output(tmp_path: Path) -> None:
    """Test multiple --input-model works with dataclass output."""
    run_multiple_input_models_and_assert(
        input_models=[
            "tests.data.python.input_model.inheritance_models:ChildA",
            "tests.data.python.input_model.inheritance_models:ChildB",
        ],
        output_path=tmp_path / "output.py",
        expected_file=EXPECTED_INPUT_MODEL_PATH / "multiple_with_dataclass_output.py",
        extra_args=["--output-model-type", "dataclasses.dataclass"],
    )


def test_input_model_multiple_non_basemodel_error(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Test error when multiple --input-model includes non-BaseModel."""
    run_multiple_input_models_error_and_assert(
        input_models=[
            "tests.data.python.input_model.inheritance_models:ChildA",
            "tests.data.python.input_model.dict_schemas:USER_SCHEMA",
        ],
        extra_args=["--output", str(tmp_path / "output.py")],
        capsys=capsys,
        expected_stderr_contains="Multiple --input-model only supports Pydantic v2 BaseModel",
    )


def test_input_model_multiple_invalid_format_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Test error when multiple --input-model has invalid format."""
    run_multiple_input_models_error_and_assert(
        input_models=[
            "tests.data.python.input_model.inheritance_models:ChildA",
            "invalid_format_no_colon",
        ],
        capsys=capsys,
        expected_stderr_contains="Invalid --input-model format",
    )


def test_input_model_multiple_file_not_found_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Test error when multiple --input-model file doesn't exist."""
    run_multiple_input_models_error_and_assert(
        input_models=[
            "tests.data.python.input_model.inheritance_models:ChildA",
            "./nonexistent_file.py:Model",
        ],
        capsys=capsys,
        expected_stderr_contains="File not found",
    )


def test_input_model_multiple_module_not_found_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Test error when multiple --input-model module doesn't exist."""
    run_multiple_input_models_error_and_assert(
        input_models=[
            "tests.data.python.input_model.inheritance_models:ChildA",
            "nonexistent_module_xyz:Model",
        ],
        capsys=capsys,
        expected_stderr_contains="Cannot find module",
    )


def test_input_model_multiple_attribute_not_found_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Test error when multiple --input-model attribute doesn't exist."""
    run_multiple_input_models_error_and_assert(
        input_models=[
            "tests.data.python.input_model.inheritance_models:ChildA",
            "tests.data.python.input_model.inheritance_models:NonexistentModel",
        ],
        capsys=capsys,
        expected_stderr_contains="has no attribute",
    )


def test_input_model_multiple_non_jsonschema_error(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Test error when multiple --input-model used with non-jsonschema type."""
    run_multiple_input_models_error_and_assert(
        input_models=[
            "tests.data.python.input_model.inheritance_models:ChildA",
            "tests.data.python.input_model.inheritance_models:ChildB",
        ],
        extra_args=["--input-file-type", "openapi", "--output", str(tmp_path / "output.py")],
        capsys=capsys,
        expected_stderr_contains="--input-file-type must be 'jsonschema'",
    )


def test_input_model_multiple_same_module(tmp_path: Path) -> None:
    """Test multiple --input-model from same module reuses module load."""
    run_multiple_input_models_and_assert(
        input_models=[
            "tests.data.python.input_model.inheritance_models:ChildA",
            "tests.data.python.input_model.inheritance_models:ChildB",
            "tests.data.python.input_model.inheritance_models:GrandChild",
        ],
        output_path=tmp_path / "output.py",
        expected_file=EXPECTED_INPUT_MODEL_PATH / "multiple_same_module.py",
        extra_args=["--output-model-type", "typing.TypedDict"],
    )


def test_input_model_multiple_file_path_format(tmp_path: Path) -> None:
    """Test multiple --input-model with file path format."""
    run_multiple_input_models_and_assert(
        input_models=[
            "tests/data/python/input_model/inheritance_models.py:ChildA",
            "tests/data/python/input_model/inheritance_models.py:ChildB",
        ],
        output_path=tmp_path / "output.py",
        expected_file=EXPECTED_INPUT_MODEL_PATH / "forked_inheritance.py",
        extra_args=["--output-model-type", "typing.TypedDict"],
    )


def test_input_model_multiple_file_path_same_basename_forward_refs(tmp_path: Path) -> None:
    """Test same-basename path modules keep separate namespaces for forward refs."""
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"
    first_dir.mkdir()
    second_dir.mkdir()
    first_model_path = first_dir / "models.py"
    second_model_path = second_dir / "models.py"
    first_model_path.write_text(
        "from __future__ import annotations\n\n"
        "from pydantic import BaseModel\n\n"
        "class UserA(BaseModel):\n"
        "    friend: FriendA\n\n"
        "class FriendA(BaseModel):\n"
        "    alpha: str\n",
        encoding="utf-8",
    )
    second_model_path.write_text(
        "from __future__ import annotations\n\n"
        "from pydantic import BaseModel\n\n"
        "class UserB(BaseModel):\n"
        "    friend: FriendB\n\n"
        "class FriendB(BaseModel):\n"
        "    beta: int\n",
        encoding="utf-8",
    )
    module_name = first_model_path.stem
    temporary_module_prefix = "_datamodel_code_generator_input_model_"
    existing_temporary_modules = {
        module_name for module_name in sys.modules if module_name.startswith(temporary_module_prefix)
    }

    with _without_sys_module(module_name):
        run_multiple_input_models_and_assert(
            input_models=[
                f"{first_model_path}:UserA",
                f"{second_model_path}:UserB",
            ],
            output_path=tmp_path / "output.py",
            expected_file=EXPECTED_INPUT_MODEL_PATH / "multiple_same_basename_paths.py",
        )

        _assert_sys_module_missing(module_name)
        _assert_sys_modules_with_prefix(temporary_module_prefix, existing_temporary_modules)


def test_input_model_multiple_file_path_format_restores_sys_modules(tmp_path: Path) -> None:
    """Test multiple path-based --input-model entries do not keep temporary modules alive."""
    model_path = tmp_path / "multiple_input_model.py"
    model_path.write_text(
        "from pydantic import BaseModel\n\n"
        "class GrandParent(BaseModel):\n"
        "    grand_field: str\n\n"
        "class Parent(GrandParent):\n"
        "    parent_field: int\n\n"
        "class ChildA(Parent):\n"
        "    child_a_field: float\n\n"
        "class ChildB(Parent):\n"
        "    child_b_field: bool\n",
        encoding="utf-8",
    )
    module_name = model_path.stem

    with _without_sys_module(module_name):
        run_multiple_input_models_and_assert(
            input_models=[
                f"{model_path}:ChildA",
                f"{model_path}:ChildB",
            ],
            output_path=tmp_path / "output.py",
            expected_file=EXPECTED_INPUT_MODEL_PATH / "forked_inheritance.py",
            extra_args=["--output-model-type", "typing.TypedDict"],
        )

        _assert_sys_module_missing(module_name)


def test_input_model_multiple_with_ref_strategy(tmp_path: Path) -> None:
    """Test multiple --input-model works with --input-model-ref-strategy."""
    run_multiple_input_models_and_assert(
        input_models=[
            "tests.data.python.input_model.inheritance_models:ChildA",
            "tests.data.python.input_model.inheritance_models:ChildB",
        ],
        output_path=tmp_path / "output.py",
        expected_file=EXPECTED_INPUT_MODEL_PATH / "forked_inheritance.py",
        extra_args=[
            "--output-model-type",
            "typing.TypedDict",
            "--input-model-ref-strategy",
            "reuse-foreign",
        ],
    )


def test_input_model_multiple_cannot_load_module_error(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test error when spec_from_file_location returns None for multiple models."""
    import importlib.util

    test_file = tmp_path / "test_model.py"
    test_file.write_text("from pydantic import BaseModel\nclass Model(BaseModel): pass")

    original_spec_from_file_location = importlib.util.spec_from_file_location

    def mock_spec(*args: object, **kwargs: object) -> None:
        if hasattr(mock_spec, "called"):
            return None
        mock_spec.called = True  # type: ignore[attr-defined]
        return original_spec_from_file_location(*args, **kwargs)

    monkeypatch.setattr(importlib.util, "spec_from_file_location", mock_spec)

    run_multiple_input_models_error_and_assert(
        input_models=[
            "tests/data/python/input_model/inheritance_models.py:ChildA",
            f"{test_file}:Model",
        ],
        extra_args=["--output", str(tmp_path / "output.py")],
        capsys=capsys,
        expected_stderr_contains="Cannot load module",
    )


def test_input_model_multiple_import_error(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test error when module import fails for multiple models."""
    import importlib
    import importlib.util

    class FakeSpec:
        name = "fake_module"

    original_find_spec = importlib.util.find_spec
    original_import_module = importlib.import_module
    call_count = 0

    def fake_find_spec(name: str, *args: object, **kwargs: object) -> FakeSpec | None:
        nonlocal call_count
        call_count += 1
        if "nonexistent_import_module" in name:
            return FakeSpec()
        return original_find_spec(name, *args, **kwargs)

    def fake_import_module(name: str, *args: object, **kwargs: object) -> object:
        if "nonexistent_import_module" in name:
            msg = "fake import error"
            raise ImportError(msg)
        return original_import_module(name, *args, **kwargs)

    monkeypatch.setattr(importlib.util, "find_spec", fake_find_spec)
    monkeypatch.setattr(importlib, "import_module", fake_import_module)

    run_multiple_input_models_error_and_assert(
        input_models=[
            "tests.data.python.input_model.inheritance_models:ChildA",
            "nonexistent_import_module:Model",
        ],
        capsys=capsys,
        expected_stderr_contains="Cannot import module",
    )


def test_input_model_empty_child_no_properties(
    tmp_path: Path,
) -> None:
    """Test inheritance with empty child that adds no properties."""
    run_multiple_input_models_and_assert(
        input_models=["tests.data.python.input_model.inheritance_models:EmptyChild"],
        output_path=tmp_path / "output.py",
        expected_file=EXPECTED_INPUT_MODEL_PATH / "empty_child_no_properties.py",
    )


def test_input_model_optional_only_child_no_required(
    tmp_path: Path,
) -> None:
    """Test inheritance with child that adds only optional fields."""
    run_multiple_input_models_and_assert(
        input_models=["tests.data.python.input_model.inheritance_models:OptionalOnlyChild"],
        output_path=tmp_path / "output.py",
        expected_file=EXPECTED_INPUT_MODEL_PATH / "optional_only_child_no_required.py",
    )


def test_input_model_cwd_already_in_path(
    tmp_path: Path,
) -> None:
    """Test that cwd is not duplicated in sys.path when already present."""
    from pathlib import Path as _Path

    cwd = str(_Path.cwd())
    initial_count = sys.path.count(cwd)

    run_multiple_input_models_and_assert(
        input_models=[
            "tests.data.python.input_model.inheritance_models:ChildA",
            "tests.data.python.input_model.inheritance_models:ChildB",
        ],
        output_path=tmp_path / "output.py",
        expected_file=EXPECTED_INPUT_MODEL_PATH / "multiple_with_pydantic_output.py",
        extra_args=["--output-model-type", "pydantic_v2.BaseModel"],
    )
    final_count = sys.path.count(cwd)
    assert final_count <= initial_count + 1


def test_input_model_multiple_py_file_without_path_separator(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test loading .py file without path separator (just filename.py)."""
    model_content = """
from pydantic import BaseModel

class TempModel(BaseModel):
    value: str
"""
    temp_file = tmp_path / "temp_model.py"
    temp_file.write_text(model_content)

    monkeypatch.chdir(tmp_path)

    run_multiple_input_models_and_assert(
        input_models=[
            "tests.data.python.input_model.inheritance_models:ChildA",
            "temp_model.py:TempModel",
        ],
        output_path=tmp_path / "output.py",
        expected_file=EXPECTED_INPUT_MODEL_PATH / "multiple_py_file_without_path_separator.py",
    )


def test_input_model_config_string_coercion(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that string input_model in config is coerced to list."""
    config_content = """\
[tool.datamodel-codegen]
input-model = "tests.data.python.input_model.inheritance_models:NoInheritance"
output-model-type = "typing.TypedDict"
"""
    config_file = tmp_path / "pyproject.toml"
    config_file.write_text(config_content)
    monkeypatch.chdir(tmp_path)

    output_path = tmp_path / "output.py"
    with freeze_time(TIMESTAMP):
        run_main_with_args(
            ["--output", str(output_path)],
            use_parsed_source_cache=False,
            use_builtin_default_formatter=False,
            isolate_model_template_cache=True,
        )
    assert_output(
        output_path.read_text(encoding="utf-8"),
        EXPECTED_INPUT_MODEL_PATH / "no_inheritance.py",
    )


def test_input_model_output_model_type_default() -> None:
    """Test that output_model_type defaults to PydanticV2BaseModel when not specified."""
    from datamodel_code_generator import InputFileType
    from datamodel_code_generator.input_model import load_model_schema

    schema = load_model_schema(
        ["tests.data.python.input_model.inheritance_models:NoInheritance"],
        InputFileType.JsonSchema,
        None,
        None,
    )
    assert schema.get("title") == "NoInheritance"
    assert "properties" in schema


# ============================================================================
# Unit tests for helper functions (coverage)
# ============================================================================


def test_simple_type_name_none_type() -> None:
    """Test _simple_type_name with NoneType."""
    from datamodel_code_generator.input_model import _simple_type_name

    result = _simple_type_name(type(None))
    assert result == "None"


def test_simple_type_name_generic_type() -> None:
    """Test _simple_type_name with generic type (has origin)."""
    from datamodel_code_generator.input_model import _simple_type_name

    result = _simple_type_name(list[str])
    assert result == "list[str]"


def test_full_type_name_string_annotation() -> None:
    """Test _full_type_name with string annotation."""
    from datamodel_code_generator.input_model import _full_type_name

    result = _full_type_name("SomeType")  # pyright: ignore[reportArgumentType]
    assert result == "SomeType"


def test_full_type_name_forward_ref() -> None:
    """Test _full_type_name with ForwardRef."""
    from typing import ForwardRef

    from datamodel_code_generator.input_model import _full_type_name

    ref = ForwardRef("MyClass")
    result = _full_type_name(ref)  # pyright: ignore[reportArgumentType]
    assert result == "MyClass"


def test_full_type_name_generic_no_args() -> None:
    """Test _full_type_name with generic type that has no args (covers line 365)."""
    from datamodel_code_generator.input_model import _full_type_name

    # Create a GenericAlias with origin=list but args=() - hits line 365
    # list.__class_getitem__(()) creates list[()] which has origin but no args
    generic_with_no_args = list.__class_getitem__(())
    result = _full_type_name(generic_with_no_args)  # pyright: ignore[reportArgumentType]
    assert result == "list"


def test_full_type_name_typing_special() -> None:
    """Test _full_type_name with typing module special forms."""
    from typing import Any

    from datamodel_code_generator.input_model import _full_type_name

    result = _full_type_name(Any)  # pyright: ignore[reportArgumentType]
    assert result == "Any"


def test_serialize_python_type_full_annotated() -> None:
    """Test _serialize_python_type_full with Annotated type."""
    from typing import Annotated

    from datamodel_code_generator.input_model import _serialize_python_type_full

    # Annotated with a custom type
    result = _serialize_python_type_full(Annotated[int, "some_metadata"])
    assert result == "int"


def test_serialize_python_type_full_wraps_invalid_runtime_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Translate runtime-IR validation failures at the input-model boundary."""
    from datamodel_code_generator import input_model as input_model_module

    def raise_invalid_runtime_type(_tp: object, *, full_name: bool = False) -> None:
        del full_name
        msg = "invalid runtime type"
        raise ValueError(msg)

    monkeypatch.setattr(input_model_module, "_runtime_python_type_expr", raise_invalid_runtime_type)

    with pytest.raises(input_model_module.Error, match="invalid runtime type"):
        input_model_module._serialize_python_type_full(object())


def test_full_type_name_builtin_type() -> None:
    """Test _full_type_name with builtin type (module='builtins')."""
    from datamodel_code_generator.input_model import _full_type_name

    # int is a builtin type with module='builtins'
    result = _full_type_name(int)
    assert result == "int"


def test_full_type_name_collections_abc_type() -> None:
    """Test _full_type_name with collections.abc type."""
    from collections.abc import Iterable

    from datamodel_code_generator.input_model import _full_type_name

    # Iterable is from collections.abc
    result = _full_type_name(Iterable)  # pyright: ignore[reportArgumentType]
    assert result == "Iterable"


INPUT_UNION_EXPECTED = Path(__file__).parent / "data" / "expected" / "main" / "input_model" / "union_annotations"


INPUT_UNION_INPUT_MODULE = "tests.data.python.input_model.union_annotations"


@pytest.mark.parametrize("model_name", CASES)
@pytest.mark.parametrize("entrypoint", ["cli", "api"])
@pytest.mark.parametrize("formatter", ["external", "builtin"])
def test_input_model_union_branch_provenance(model_name: str, entrypoint: str, formatter: str, tmp_path: Path) -> None:
    """Keep union order, exact generated bytes, and source runtime acceptance."""
    source = f"{INPUT_UNION_INPUT_MODULE}:{model_name}"
    output = tmp_path / "output.py"
    formatters = ["builtin"] if formatter == "builtin" else ["black", "isort"]
    base_class = f"{INPUT_UNION_INPUT_MODULE}.ArbitraryBase" if model_name == "MultipleUnsupported" else None
    extra_args = ["--base-class", base_class] if base_class else []
    expected_name = f"{model_name}_builtin" if formatter == "builtin" and base_class else model_name
    if entrypoint == "cli":
        run_input_model_and_assert(
            input_model=source,
            output_path=output,
            expected_file=INPUT_UNION_EXPECTED / f"{expected_name}.py",
            extra_args=["--disable-timestamp", "--formatters", *formatters, *extra_args],
        )
    else:
        run_generate_and_assert(
            input_=load_model_schema([source], InputFileType.JsonSchema),
            expected_file=INPUT_UNION_EXPECTED / f"{expected_name}.py",
            config=GenerateConfig(
                base_class=base_class or "",
                input_file_type=InputFileType.JsonSchema,
                input_filename="<stdin>",
                output_model_type=DataModelType.PydanticV2BaseModel,
                disable_timestamp=True,
                output=output,
                formatters=[Formatter(value) for value in formatters],
            ),
        )
    schema = load_model_schema([source], InputFileType.JsonSchema)
    assert_output(json.dumps(schema, indent=2), INPUT_UNION_EXPECTED / f"{model_name}_schema.txt")
    records = {}
    with _generated_model(output, "_generated_union_annotations", model_name) as generated:
        for label in CASES[model_name]:
            payload = {"value": VALUES[label]}
            observations = {}
            for name, model in (("source", getattr(union_annotations, model_name)), ("generated", generated)):
                with assert_inputs_not_mutated(payload):
                    try:
                        result = model.model_validate(payload)
                    except ValidationError:
                        observations[name] = "rejected"
                    else:
                        observations[name] = describe(result.model_dump())
            records[label] = observations
    assert_output(json.dumps(records, indent=2), INPUT_UNION_EXPECTED / f"{model_name}_runtime.txt")


@pytest.mark.parametrize("model_name", ["Serializable", "Mixed", "Container", "Nested"])
@pytest.mark.parametrize("entrypoint", ["cli", "api"])
@pytest.mark.parametrize("formatter", ["external", "builtin"])
def test_input_model_union_caller_extensions(model_name: str, entrypoint: str, formatter: str, tmp_path: Path) -> None:
    """Consume generated provenance without discarding similarly named user metadata."""
    from tests.data.python.input_model import union_extensions

    source = f"tests.data.python.input_model.union_extensions:{model_name}"
    output = tmp_path / "output.py"
    formatters = ["builtin"] if formatter == "builtin" else ["black", "isort"]
    if entrypoint == "cli":
        run_input_model_and_assert(
            input_model=source,
            output_path=output,
            expected_file=INPUT_UNION_EXPECTED / f"extension_{model_name}.py",
            extra_args=["--disable-timestamp", "--formatters", *formatters],
        )
    else:
        run_generate_and_assert(
            input_=load_model_schema([source], InputFileType.JsonSchema),
            expected_file=INPUT_UNION_EXPECTED / f"extension_{model_name}.py",
            config=GenerateConfig(
                input_file_type=InputFileType.JsonSchema,
                input_filename="<stdin>",
                disable_timestamp=True,
                output=output,
                formatters=[Formatter(value) for value in formatters],
            ),
        )
    schema = load_model_schema([source], InputFileType.JsonSchema)
    assert_output(json.dumps(schema, indent=2), INPUT_UNION_EXPECTED / f"extension_{model_name}_schema.txt")
    if model_name == "Serializable":
        assert_output(
            json.dumps(union_extensions.Serializable.model_json_schema(), indent=2),
            INPUT_UNION_EXPECTED / f"extension_{model_name}_schema.txt",
        )
    with _generated_model(output, "_generated_union_extensions", model_name) as generated:
        for model in (getattr(union_extensions, model_name), generated):
            observations = []
            for value in (1, "text", int, None, [int]):
                payload = {"value": value}
                with assert_inputs_not_mutated(payload):
                    try:
                        result = model.model_validate(payload)
                    except ValidationError:
                        observations.append("rejected")
                    else:
                        observations.append(describe(result.model_dump()))
            assert_output(
                json.dumps(observations, indent=2), INPUT_UNION_EXPECTED / f"extension_{model_name}_runtime.txt"
            )


INPUT_OVERRIDE_EXPECTED = Path(__file__).parent / "data" / "expected" / "main" / "input_model" / "inherited_overrides"


INPUT_OVERRIDE_INPUT_MODULE = "tests.data.python.input_model.inherited_overrides"


@pytest.mark.parametrize("model_name", INHERITED_OVERRIDE_CASES)
@pytest.mark.parametrize("entrypoint", ["cli", "api"])
def test_input_model_inherited_overrides(model_name: str, entrypoint: str, tmp_path: Path) -> None:
    """Preserve effective child fields, generated base classes, and native behavior."""
    source = f"{INPUT_OVERRIDE_INPUT_MODULE}:{model_name}"
    output = tmp_path / "output.py"
    use_builtin = os.environ.get(TEST_DEFAULT_FORMATTER_ENV) == BUILTIN_FORMATTER_VALUE
    formatters = [Formatter.BUILTIN] if use_builtin else [Formatter.ISORT, Formatter.BLACK]
    schema_path = (
        Path(__file__).parent / "data" / "jsonschema" / "input_model_caller_override_extension.json"
        if model_name == "RawSchemaIntersection"
        else None
    )
    expected_file = INPUT_OVERRIDE_EXPECTED / f"{model_name}{'_builtin' if use_builtin else ''}.py"
    extra_args = [
        "--disable-timestamp",
        "--strict-nullable",
        "--generate-schema-validators",
        "--formatters",
        *(formatter.value for formatter in formatters),
    ]
    if entrypoint == "cli":
        if schema_path:
            run_main_and_assert(
                input_path=schema_path,
                input_file_type="jsonschema",
                output_path=output,
                expected_file=expected_file,
                extra_args=extra_args,
            )
        else:
            run_input_model_and_assert(
                input_model=source,
                output_path=output,
                expected_file=expected_file,
                extra_args=extra_args,
            )
    else:
        schema = (
            json.loads(schema_path.read_text())
            if schema_path
            else load_model_schema([source], InputFileType.JsonSchema)
        )
        with assert_inputs_not_mutated(schema):
            run_generate_and_assert(
                input_=schema,
                expected_file=expected_file,
                config=GenerateConfig(
                    input_file_type=InputFileType.JsonSchema,
                    input_filename=schema_path.name if schema_path else "<stdin>",
                    output_model_type=DataModelType.PydanticV2BaseModel,
                    disable_timestamp=True,
                    strict_nullable=True,
                    generate_schema_validators=True,
                    formatters=formatters,
                    output=output,
                ),
            )
    schema = (
        json.loads(schema_path.read_text()) if schema_path else load_model_schema([source], InputFileType.JsonSchema)
    )
    assert_output(json.dumps(schema, indent=2), INPUT_OVERRIDE_EXPECTED / f"{model_name}_schema.txt")
    records = []
    with _generated_model(output, "_generated_inherited_overrides", model_name) as generated:
        records.append({
            "bases": [base.__name__ for base in generated.__bases__],
            "fields": list(generated.model_fields),
        })
        for payload in INHERITED_OVERRIDE_CASES[model_name]:
            observations = {"payload": payload}
            for name, model in (("source", getattr(inherited_overrides, model_name)), ("generated", generated)):
                with assert_inputs_not_mutated(payload):
                    try:
                        result = model.model_validate(payload)
                    except ValidationError as error:
                        observations[name] = {"errors": [item["type"] for item in error.errors()]}
                    else:
                        observations[name] = result.model_dump(mode="json", by_alias=True)
            records.append(observations)
    assert_output(json.dumps(records, indent=2), INPUT_OVERRIDE_EXPECTED / f"{model_name}_runtime.txt")


@pytest.mark.parametrize("formatter", ["builtin", "external"])
@pytest.mark.parametrize("entrypoint", ["cli", "api"])
@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("strategy", ["regenerate-all", "reuse-all", "reuse-foreign"])
@pytest.mark.parametrize(
    "case",
    ["nested", "roots", "shared", "plain", "identical", "metadata", "recursive", "generic", "suffixes", "suffix_roots"],
)
def test_input_model_definition_collisions(
    tmp_path: Path,
    entrypoint: str,
    reverse: bool,
    strategy: str,
    case: str,
    formatter: str,
) -> None:
    """Keep colliding and shared runtime types attached to their own definitions."""
    from datamodel_code_generator import GenerateConfig, InputFileType
    from datamodel_code_generator.enums import InputModelRefStrategy
    from datamodel_code_generator.format import Formatter
    from datamodel_code_generator.input_model import load_model_schema

    collision_case = json.loads(
        (EXPECTED_INPUT_MODEL_PATH.parents[2] / "python/input_model/equal_identity.json").read_text()
    )["definition_collisions"][case]
    roots = collision_case["roots"]
    sides = "abc"[: len(roots)]
    paths = [f"tests.data.python.input_model.collision_{side}:{root}" for side, root in zip(sides, roots, strict=True)]
    if reverse:
        paths.reverse()
    settings = Path(__file__).parent / "data/python/input_model/collision_settings"
    (tmp_path / "pyproject.toml").write_text((settings / "pyproject.toml").read_text())
    formatters = ["builtin"] if formatter == "builtin" else ["black", "isort"]
    output = tmp_path / "output.py"
    expected_strategy = "regenerate-all" if strategy == "regenerate-all" else "reuse-all"
    expected = EXPECTED_INPUT_MODEL_PATH / f"collisions_{case}_{'ba' if reverse else 'ab'}_{expected_strategy}.py"
    if entrypoint == "cli":
        run_main_with_args(
            _input_model_args(
                paths,
                output_path=output,
                extra_args=[
                    "--disable-timestamp",
                    "--input-model-ref-strategy",
                    strategy,
                    "--formatters",
                    *formatters,
                ],
            ),
        )
    else:
        config = GenerateConfig(
            input_file_type=InputFileType.JsonSchema,
            disable_timestamp=True,
            input_filename="<stdin>",
            output=output,
            settings_path=tmp_path,
            formatters=[Formatter(value) for value in formatters],
        )
        schema = load_model_schema(paths, InputFileType.JsonSchema, InputModelRefStrategy(strategy))
        run_generate_and_assert(input_=schema, config=config, expected_file=expected)
    if entrypoint == "cli":
        assert_output(output.read_text(), expected)
    payloads = collision_case["payloads"]
    with _generated_model(output, "collision_output", "Model") as generated:
        module = sys.modules[generated.__module__]
        for side, root, payload in zip(sides, roots, payloads, strict=True):
            source_model = getattr(importlib.import_module(f"tests.data.python.input_model.collision_{side}"), root)
            assert source_model.model_validate(payload).model_dump(mode="json", exclude_unset=True) == payload
            result = module.Model.model_validate(payload).root.model_dump(mode="json", exclude_unset=True)
            assert result == payload
        if case == "identical":
            assert (
                module.SameRootA.model_fields["data"].annotation is not module.SameRootB.model_fields["data"].annotation
            )
        elif case == "shared":
            assert module.RootA.model_fields["data"].annotation is module.SharedRoot.model_fields["shared"].annotation


@pytest.mark.parametrize(
    "case", ["Both", "Reversed", "Shared", "Child", "UnionRoot", "RecursiveRoot", "EnumRoot", "AliasRoot"]
)
@pytest.mark.parametrize("multiple", [False, True])
@pytest.mark.parametrize("strategy", ["regenerate-all", "reuse-all"])
@pytest.mark.parametrize("entrypoint", ["cli", "api"])
@pytest.mark.parametrize("formatter", ["external", "builtin"])
def test_input_model_equal_schema_identity(
    tmp_path: Path, case: str, multiple: bool, strategy: str, entrypoint: str, formatter: str
) -> None:
    """Equal schemas retain distinct reused types and their native validators."""
    from pydantic import ValidationError

    from datamodel_code_generator import GenerateConfig, InputFileType
    from datamodel_code_generator.enums import InputModelRefStrategy
    from datamodel_code_generator.format import Formatter
    from datamodel_code_generator.input_model import load_model_schema
    from tests.main.conftest import _generated_model

    source = "tests.data.python.input_model.same_schema_roots"
    paths = [f"{source}:{case}", f"{source}:Shared"] if multiple else [f"{source}:{case}"]
    settings = Path(__file__).parent / "data/python/input_model/collision_settings"
    (tmp_path / "pyproject.toml").write_text((settings / "pyproject.toml").read_text())
    output = tmp_path / "model.py"
    formatters = ["builtin"] if formatter == "builtin" else ["black", "isort"]
    suffix = f"{case.lower()}_{'multiple' if multiple else 'single'}_{strategy}"
    if entrypoint == "cli":
        run_main_with_args(
            _input_model_args(
                paths,
                output_path=output,
                extra_args=["--disable-timestamp", "--input-model-ref-strategy", strategy, "--formatters", *formatters],
            )
        )
    else:
        config = GenerateConfig(
            input_file_type=InputFileType.JsonSchema,
            disable_timestamp=True,
            input_filename="<stdin>",
            output=output,
            settings_path=tmp_path,
            formatters=[Formatter(value) for value in formatters],
        )
        run_generate_and_assert(
            input_=load_model_schema(paths, InputFileType.JsonSchema, InputModelRefStrategy(strategy)),
            config=config,
            expected_file=EXPECTED_INPUT_MODEL_PATH / f"equal_identity_{suffix}.py",
        )
    if entrypoint == "cli":
        assert_output(output.read_text(), EXPECTED_INPUT_MODEL_PATH / f"equal_identity_{suffix}.py")
    settings = json.loads((EXPECTED_INPUT_MODEL_PATH.parents[2] / "python/input_model/equal_identity.json").read_text())
    options = settings[case]
    native = getattr(importlib.import_module(source), case)
    with _generated_model(output, f"generated_equal_identity_{case}", case) as model:
        original = native.model_validate(options["valid"])
        generated = model.model_validate(options["valid"])
        observed = {
            "values": generated.model_dump(mode="json"),
            "identities": {
                name: type(getattr(generated, name)) is type(getattr(original, name)) for name in options["fields"]
            },
        }
        assert_output(
            json.dumps(observed, indent=2), EXPECTED_INPUT_MODEL_PATH / f"equal_identity_{case.lower()}_{strategy}.txt"
        )
        if strategy == "reuse-all" and "invalid" in options:
            for candidate in (native, model):
                with pytest.raises(ValidationError):
                    candidate.model_validate(options["invalid"])


def test_input_model_equal_native_schemas() -> None:
    """The distinct native types expose equal schemas before input-model conversion."""
    from pydantic import TypeAdapter

    from tests.data.python.input_model.same_schema_roots import TYPE_PAIRS

    assert_output(
        json.dumps([TypeAdapter(left).json_schema() == TypeAdapter(right).json_schema() for left, right in TYPE_PAIRS]),
        EXPECTED_INPUT_MODEL_PATH / "equal_identity_native_schemas.txt",
    )


@pytest.mark.parametrize("entrypoint", ["cli", "api"])
@pytest.mark.parametrize("formatter", ["external", "builtin"])
@pytest.mark.parametrize(
    "case", ["Plain", "Aliased", "Validation", "Choices", "Paths", "Serialization", "Swapped", "Nested", "Hidden"]
)
def test_input_model_wire_types(tmp_path: Path, entrypoint: str, formatter: str, case: str) -> None:
    """Restore source types using the validation schema's actual property names."""
    from pydantic import ValidationError

    from datamodel_code_generator import GenerateConfig, InputFileType
    from datamodel_code_generator.format import Formatter
    from datamodel_code_generator.input_model import load_model_schema

    paths = [f"tests.data.python.input_model.wire_types:{case}"]
    (tmp_path / "pyproject.toml").write_text(
        (Path(__file__).parent / "data/python/input_model/collision_settings/pyproject.toml").read_text()
    )
    formatters = ["builtin"] if formatter == "builtin" else ["black", "isort"]
    output = tmp_path / "output.py"
    if entrypoint == "cli":
        run_main_with_args(
            _input_model_args(
                paths, output_path=output, extra_args=["--disable-timestamp", "--formatters", *formatters]
            )
        )
    else:
        config = GenerateConfig(
            input_file_type=InputFileType.JsonSchema,
            disable_timestamp=True,
            input_filename="<stdin>",
            output=output,
            settings_path=tmp_path,
            formatters=[Formatter(value) for value in formatters],
        )
        schema = load_model_schema(paths, InputFileType.JsonSchema)
        run_generate_and_assert(
            input_=schema, config=config, expected_file=EXPECTED_INPUT_MODEL_PATH / f"wire_types_{case.lower()}.py"
        )
    if entrypoint == "cli":
        assert_output(output.read_text(), EXPECTED_INPUT_MODEL_PATH / f"wire_types_{case.lower()}.py")
    with _generated_model(output, "wire_types_output", case) as model:
        values_name, callback_name = (
            ("wire_values", "wire_callback")
            if case in {"Aliased", "Validation", "Choices", "Nested"}
            else ("callback", "values")
            if case == "Swapped"
            else ("values", "callback")
        )
        payload = {"first": 1, values_name: 1 if case == "Nested" else [1, 2, 1], callback_name: int, "last": "end"}
        source_payload = dict(payload)
        if case == "Paths":
            source_payload["data"] = {"values": source_payload.pop("values")}
        source_model = getattr(importlib.import_module("tests.data.python.input_model.wire_types"), case)
        source = source_model.model_validate({"item": source_payload} if case == "Nested" else source_payload)
        result = model.model_validate({"item": payload} if case == "Nested" else payload)
        if case == "Nested":
            source, result = source.item, result.item
        assert getattr(result, values_name) == source.values
        assert type(getattr(result, values_name)) is type(source.values)
        if case != "Nested":
            assert source.values == frozenset({1, 2})
        assert getattr(result, callback_name) is source.callback is int
        assert list(type(result).model_fields) == ["first", values_name, callback_name, "last"]
        for invalid in ("not callable", 3):
            payload[callback_name] = invalid
            source_payload[callback_name] = invalid
            with pytest.raises(ValidationError):
                model.model_validate({"item": payload} if case == "Nested" else payload)
            with pytest.raises(ValidationError):
                source_model.model_validate({"item": source_payload} if case == "Nested" else source_payload)


INPUT_WIRE_SOURCE_MODULE = "tests.data.python.input_model.wire_winners"


INPUT_WIRE_CASES = json.loads(
    (EXPECTED_INPUT_MODEL_PATH.parents[2] / "python/input_model/wire_winners.json").read_text()
)


@pytest.mark.parametrize("case", INPUT_WIRE_CASES)
@pytest.mark.parametrize("entrypoint", ["cli", "api"])
@pytest.mark.parametrize("formatter", ["external", "builtin"])
def test_input_model_validation_property_winners(tmp_path: Path, case: str, entrypoint: str, formatter: str) -> None:
    """Only the field whose schema survives may supplement the emitted property."""
    options = INPUT_WIRE_CASES[case]
    formatters = ["builtin"] if formatter == "builtin" else ["black", "isort"]
    output = tmp_path / "model.py"
    paths = [f"{INPUT_WIRE_SOURCE_MODULE}:{name}" for name in options.get("models", [case])]
    model_name = options.get("model", case)
    if entrypoint == "cli":
        run_main_with_args(
            _input_model_args(
                paths, output_path=output, extra_args=["--disable-timestamp", "--formatters", *formatters]
            )
        )
    else:
        schema = load_model_schema(paths, InputFileType.JsonSchema)
        if case == "InlinedWins":
            assert_output(json.dumps(schema["examples"]), EXPECTED_INPUT_MODEL_PATH / "wire_winner_examples.txt")
        assert_output(
            json.dumps(schema, indent=2), EXPECTED_INPUT_MODEL_PATH / f"wire_winner_{case.lower()}_schema.txt"
        )
        run_generate_and_assert(
            input_=schema,
            expected_file=EXPECTED_INPUT_MODEL_PATH / f"wire_winner_{case.lower()}.py",
            config=GenerateConfig(
                input_file_type=InputFileType.JsonSchema,
                output=output,
                disable_timestamp=True,
                input_filename="<stdin>",
                formatters=[Formatter(value) for value in formatters],
            ),
        )
    if entrypoint == "cli":
        assert_output(output.read_text(), EXPECTED_INPUT_MODEL_PATH / f"wire_winner_{case.lower()}.py")
    payload: dict[str, Any] = {"first": 1, "shared": int if options.get("callable") else [1, 1, 2], "last": "end"}
    if case == "PathWins":
        payload["values"] = payload.pop("shared")
        payload["data"] = {"values": [7, 7]}
    elif case in {"Serialization", "PlainData", "PlainTyped"}:
        payload["values"] = payload.pop("shared")
        if case == "Serialization":
            payload["items"] = [3, 3, 4]
    elif case == "Populated":
        payload.update(values=[7, 7], items=[8, 8])
    source_type = getattr(importlib.import_module(INPUT_WIRE_SOURCE_MODULE), model_name)
    native = TypeAdapter(source_type).validate_python({"item": payload} if options.get("nested") else payload)
    with _generated_model(output, f"generated_winner_{case}", model_name) as model:
        generated = model.model_validate({"item": payload} if options.get("nested") else payload)
        if options.get("nested"):
            native, generated = native.item, generated.item
        for instance, fields, order in (
            (native, options["fields"], options["order"]),
            (generated, {name: name for name in options["fields"]}, list(type(generated).model_fields)),
        ):
            values = {}
            for wire_name, attribute_name in fields.items():
                value = instance[attribute_name] if isinstance(instance, dict) else getattr(instance, attribute_name)
                values[wire_name] = {
                    "type": type(value).__name__,
                    "value": value(7) if callable(value) else sorted(value) if isinstance(value, frozenset) else value,
                }
            assert_output(
                json.dumps({"order": order, "values": values}, indent=2),
                EXPECTED_INPUT_MODEL_PATH / f"wire_winner_{case.lower()}_runtime.txt",
            )
        if options.get("reject_noncallable"):
            payload["shared"] = "not callable"
            with pytest.raises(ValidationError):
                TypeAdapter(source_type).validate_python(payload)
            with pytest.raises(ValidationError):
                model.model_validate(payload)


@pytest.mark.parametrize("case", ["UserExtensions", "CollisionExtensions", "ExtensionContainer"])
def test_validation_property_user_extensions(case: str) -> None:
    """Internal ownership metadata preserves native schema extensions and instance data."""
    source_type = getattr(importlib.import_module(INPUT_WIRE_SOURCE_MODULE), case)
    native_schema = TypeAdapter(source_type).json_schema()
    schema = load_model_schema([f"{INPUT_WIRE_SOURCE_MODULE}:{case}"], InputFileType.JsonSchema)
    observed = schema["$defs"]["CollisionExtensions"] if case == "ExtensionContainer" else schema
    expected = native_schema["$defs"]["CollisionExtensions"] if case == "ExtensionContainer" else native_schema
    for candidate in (observed, expected):
        keys = ("x-datamodel-code-generator-field-name", "x-datamodel-code-generator-field-names")
        records = {
            "root": {key: candidate[key] for key in (*keys, "examples")},
            "property": {key: candidate["properties"]["shared"][key] for key in keys},
            "default": candidate["properties"]["metadata"]["default"],
        }
        assert_output(
            json.dumps(records, indent=2), EXPECTED_INPUT_MODEL_PATH / f"wire_winner_{case.lower()}_extensions.txt"
        )


@pytest.mark.parametrize("roots", ["ordinary", "before", "after", "nested", "nested_duplicate", "inline_duplicate"])
@pytest.mark.parametrize("entrypoint", ["cli", "api"])
@pytest.mark.parametrize("formatter", ["external", "builtin"])
def test_validation_property_owner_scope(tmp_path: Path, roots: str, entrypoint: str, formatter: str) -> None:
    """Ordinary roots do not install owner hooks or inherit cleanup from prior roots."""
    import sys

    module_name = "tests.data.python.input_model.wire_owner_scope"
    names = {
        "ordinary": ["Plain"],
        "before": ["Plain", "Duplicate"],
        "after": ["Duplicate", "Plain"],
        "nested": ["Container", "Plain"],
        "nested_duplicate": ["NestedDuplicate", "Plain"],
        "inline_duplicate": ["InlineDuplicate", "Plain"],
    }[roots]
    paths = [f"{module_name}:{name}" for name in names]
    source_module = importlib.import_module(module_name)
    source_module.owner_calls.clear()

    formatters = ["builtin"] if formatter == "builtin" else ["black", "isort"]
    output = tmp_path / "model.py"
    previous = sys.getprofile()
    sys.setprofile(source_module.record_owner_calls)
    try:
        if entrypoint == "cli":
            run_main_with_args(
                _input_model_args(
                    paths, output_path=output, extra_args=["--disable-timestamp", "--formatters", *formatters]
                )
            )
        else:
            run_generate_and_assert(
                input_=load_model_schema(paths, InputFileType.JsonSchema),
                expected_file=EXPECTED_INPUT_MODEL_PATH / f"wire_owner_scope_{roots}.py",
                config=GenerateConfig(
                    input_file_type=InputFileType.JsonSchema,
                    output=output,
                    disable_timestamp=True,
                    input_filename="<stdin>",
                    formatters=[Formatter(value) for value in formatters],
                ),
            )
    finally:
        sys.setprofile(previous)
    if entrypoint == "cli":
        assert_output(output.read_text(), EXPECTED_INPUT_MODEL_PATH / f"wire_owner_scope_{roots}.py")
    assert_output(
        json.dumps(dict(source_module.owner_calls), indent=2),
        EXPECTED_INPUT_MODEL_PATH / f"wire_owner_scope_{roots}_counts.txt",
    )
    with _generated_model(output, f"owner_scope_{roots}", names[0]) as generated:
        module = sys.modules[generated.__module__]
        for name in names:
            payload = {"first": 1, "second": 2, "third": 3} if name == "Plain" else {"wire": [1, 1, 2]}
            if name in {"Container", "NestedDuplicate", "InlineDuplicate"}:
                payload = {"item": payload}
            for model in (getattr(source_module, name), getattr(module, name)):
                instance = model.model_validate(payload)
                assert_output(
                    json.dumps(instance.model_dump(mode="json", by_alias=True), separators=(",", ":")),
                    EXPECTED_INPUT_MODEL_PATH / f"wire_owner_scope_{name.lower()}_runtime.txt",
                )


@pytest.mark.parametrize("entrypoint", ["cli", "api"])
@pytest.mark.parametrize("formatter", ["builtin", "external"])
@pytest.mark.parametrize("strategy", ["regenerate-all", "reuse-all", "reuse-foreign"])
@pytest.mark.parametrize("case", ["nested", "direct", "export", "alias", "multiple", "reverse"])
def test_input_model_nested_reuse(tmp_path: Path, entrypoint: str, formatter: str, strategy: str, case: str) -> None:
    """Reuse exact nested identities without importing their owners as modules."""
    from pydantic import ValidationError

    from datamodel_code_generator import DataModelType, GenerateConfig, InputFileType
    from datamodel_code_generator.enums import InputModelRefStrategy
    from datamodel_code_generator.format import Formatter
    from datamodel_code_generator.input_model import load_model_schema

    fixture_dir = Path(__file__).parent / "data/python/input_model"
    record = next(
        item for item in json.loads((fixture_dir / "nested_reuse_cases.json").read_text()) if item["name"] == case
    )
    paths = [f"tests.data.python.input_model.{source}" for source in record["sources"]]
    (tmp_path / "pyproject.toml").write_text((fixture_dir / "collision_settings/pyproject.toml").read_text())
    formatters = ["builtin"] if formatter == "builtin" else ["black", "isort"]
    output = tmp_path / "output.py"
    if entrypoint == "cli":
        run_main_with_args(
            _input_model_args(
                paths,
                output_path=output,
                extra_args=["--disable-timestamp", "--input-model-ref-strategy", strategy, "--formatters", *formatters],
            )
        )
    else:
        config = GenerateConfig(
            input_file_type=InputFileType.JsonSchema,
            disable_timestamp=True,
            input_filename="<stdin>",
            output=output,
            settings_path=tmp_path,
            formatters=[Formatter(value) for value in formatters],
        )
        schema = load_model_schema(
            paths, InputFileType.JsonSchema, InputModelRefStrategy(strategy), DataModelType.PydanticV2BaseModel
        )
        run_generate_and_assert(
            input_=schema, config=config, expected_file=EXPECTED_INPUT_MODEL_PATH / f"nested_reuse_{case}_{strategy}.py"
        )
    if entrypoint == "cli":
        assert_output(output.read_text(), EXPECTED_INPUT_MODEL_PATH / f"nested_reuse_{case}_{strategy}.py")
    with _generated_model(output, "nested_reuse_output", paths[0].split(":")[1]):
        module = sys.modules["nested_reuse_output"]
        for path, payload in zip(paths, record["payloads"], strict=True):
            module_name, model_name = path.split(":")
            native_model = getattr(importlib.import_module(module_name), model_name)
            native = native_model.model_validate(payload)
            generated_model = getattr(module, model_name)
            generated = generated_model.model_validate(payload)
            assert generated.model_dump(mode="json", exclude_unset=True) == native.model_dump(
                mode="json", exclude_unset=True
            )
            assert list(generated_model.model_fields) == list(native_model.model_fields)
            if len(paths) > 1:
                assert module.Model.model_validate(payload).root.model_dump(
                    mode="json", exclude_unset=True
                ) == native.model_dump(mode="json", exclude_unset=True)
            with pytest.raises(ValidationError):
                generated_model.model_validate({"child": {}})
            with pytest.raises(ValidationError):
                native_model.model_validate({"child": {}})
            if strategy != "regenerate-all":
                assert type(generated.child) is type(native.child)
            if model_name == "NestedRoot" and strategy != "regenerate-all":
                assert type(generated.leaf) is type(native.leaf)
                assert type(generated.kind) is type(native.kind)
                assert type(generated.node.child) is type(native.node.child)
                if strategy == "reuse-all":
                    assert type(generated.record) is type(native.record)


@pytest.mark.parametrize("entrypoint", ["cli", "api"])
@pytest.mark.parametrize("formatter", ["builtin", "external"])
def test_input_model_nested_reuse_type_binding(tmp_path: Path, entrypoint: str, formatter: str) -> None:
    """Bind preserved containers to nested definitions using the same runtime symbol."""
    from pydantic import ValidationError

    from datamodel_code_generator import GenerateConfig, InputFileType
    from datamodel_code_generator.format import Formatter
    from tests.data.python.input_model.nested_reuse import ContainerRoot, Outer

    fixture = Path(__file__).parent / "data/jsonschema/nested_reuse_type.json"
    output = tmp_path / "output.py"
    (tmp_path / "pyproject.toml").write_text(
        (Path(__file__).parent / "data/python/input_model/collision_settings/pyproject.toml").read_text()
    )
    formatters = ["builtin"] if formatter == "builtin" else ["black", "isort"]
    if entrypoint == "cli":
        run_main_with_args([
            "--input",
            str(fixture),
            "--output",
            str(output),
            "--input-file-type",
            "jsonschema",
            "--disable-timestamp",
            "--formatters",
            *formatters,
        ])
    else:
        run_generate_and_assert(
            input_=fixture,
            expected_file=EXPECTED_INPUT_MODEL_PATH / "nested_reuse_type.py",
            config=GenerateConfig(
                input_file_type=InputFileType.JsonSchema,
                output=output,
                disable_timestamp=True,
                settings_path=tmp_path,
                formatters=[Formatter(value) for value in formatters],
            ),
        )
    if entrypoint == "cli":
        assert_output(output.read_text(), EXPECTED_INPUT_MODEL_PATH / "nested_reuse_type.py")
    with _generated_model(output, "nested_reuse_type_output", "ContainerRoot") as model:
        payload = {"items": [{"value": 1}]}
        native = ContainerRoot.model_validate(payload)
        generated = model.model_validate(payload)
        assert type(generated.items) is type(native.items)
        assert type(generated.items[0]) is Outer.Inner
        assert generated.model_dump(mode="json", exclude_unset=True) == native.model_dump(
            mode="json", exclude_unset=True
        )
        with pytest.raises(ValidationError):
            model.model_validate({"items": [{"value": "bad"}]})


@pytest.mark.parametrize("entrypoint", ["cli", "api"])
@pytest.mark.parametrize("formatter", ["builtin", "external"])
@pytest.mark.parametrize("strategy", ["regenerate-all", "reuse-all", "reuse-foreign"])
@pytest.mark.parametrize(
    "case",
    [
        "Deleted",
        "Scalar",
        "Changed",
        "Descriptor",
        "Live",
        "Lazy",
        "Redirected",
        "Raising",
        "Chained",
        "ModuleRedirected",
        "Proxy",
    ],
)
def test_input_model_dynamic_nested_exports(
    tmp_path: Path, entrypoint: str, formatter: str, strategy: str, case: str
) -> None:
    """Check owner identity while preserving existing dynamic short exports."""
    from pydantic import ValidationError

    from datamodel_code_generator import DataModelType, GenerateConfig, InputFileType
    from datamodel_code_generator.enums import InputModelRefStrategy
    from datamodel_code_generator.format import Formatter
    from datamodel_code_generator.input_model import load_model_schema
    from tests.data.python.input_model import nested_dynamic_exports

    output = tmp_path / "output.py"
    (tmp_path / "pyproject.toml").write_text(
        (Path(__file__).parent / "data/python/input_model/collision_settings/pyproject.toml").read_text()
    )
    paths = [f"tests.data.python.input_model.nested_dynamic_exports:{case}Root"]
    formatters = ["builtin"] if formatter == "builtin" else ["black", "isort"]
    nested_dynamic_exports.calls.clear()
    if entrypoint == "cli":
        run_main_with_args(
            _input_model_args(
                paths,
                output_path=output,
                extra_args=["--disable-timestamp", "--input-model-ref-strategy", strategy, "--formatters", *formatters],
            )
        )
    else:
        config = GenerateConfig(
            input_file_type=InputFileType.JsonSchema,
            disable_timestamp=True,
            input_filename="<stdin>",
            output=output,
            settings_path=tmp_path,
            formatters=[Formatter(value) for value in formatters],
        )
        schema = load_model_schema(
            paths, InputFileType.JsonSchema, InputModelRefStrategy(strategy), DataModelType.PydanticV2BaseModel
        )
        if case == "Deleted":
            assert schema["x-caller-root"] == "kept"
        run_generate_and_assert(
            input_=schema,
            config=config,
            expected_file=EXPECTED_INPUT_MODEL_PATH / f"dynamic_nested_{case}_{strategy}.py",
        )
    assert nested_dynamic_exports.calls == []
    if entrypoint == "cli":
        assert_output(output.read_text(), EXPECTED_INPUT_MODEL_PATH / f"dynamic_nested_{case}_{strategy}.py")
    with _generated_model(output, "dynamic_nested_output", f"{case}Root") as model:
        native = getattr(nested_dynamic_exports, f"{case}Root")(child={"value": 1})
        generated = model(child={"value": 1})
        assert generated.model_dump() == native.model_dump() == {"child": {"value": 1}}
        assert list(type(generated).model_fields) == ["child"]
        with pytest.raises(ValidationError):
            type(native).model_validate({"child": {"value": "invalid"}})
        with pytest.raises(ValidationError):
            type(generated).model_validate({"child": {"value": "invalid"}})
        if strategy != "regenerate-all":
            assert type(generated.child) is type(native.child) is nested_dynamic_exports.exports[case]
        assert nested_dynamic_exports.calls == (
            ["__path__", case] if strategy != "regenerate-all" and case not in {"Live", "Lazy", "Proxy"} else []
        )


INPUT_GENERIC_FIXTURES = DATA_PATH / "python/input_model"


INPUT_GENERIC_EXPECTED = EXPECTED_MAIN_PATH / "input_model/generic_reuse"


INPUT_GENERIC_CASES = json.loads((INPUT_GENERIC_FIXTURES / "generic_reuse_cases.json").read_text())


@pytest.mark.parametrize(
    ("record", "strategy", "output_model", "preserved"),
    [
        *(
            pytest.param(record, strategy, output_model, False, id=f"{strategy}-{record['name']}-{output_model}")
            for record in INPUT_GENERIC_CASES
            for strategy in record.get("strategies", ["regenerate-all", "reuse-all", "reuse-foreign"])
            for output_model in record.get("output_models", ["pydantic_v2.BaseModel"])
        ),
        pytest.param(INPUT_GENERIC_CASES[0], "reuse-all", "pydantic_v2.BaseModel", True, id="preserved"),
    ],
)
@pytest.mark.parametrize("entrypoint", ["cli", "api"])
@pytest.mark.parametrize("formatter", ["builtin", "external"])
def test_python_generic_reuse(  # noqa: PLR0912, PLR0914
    record: dict, strategy: str, output_model: str, entrypoint: str, formatter: str, tmp_path: Path, *, preserved: bool
) -> None:
    """Preserve original validators and specialization identity without changing regeneration."""
    paths = ["tests.data.python.input_model." + source for source in record["sources"]]
    (tmp_path / "pyproject.toml").write_text((INPUT_GENERIC_FIXTURES / "collision_settings/pyproject.toml").read_text())
    formatters = ["builtin"] if formatter == "builtin" else ["black", "isort"]
    output = tmp_path / "output.py"
    expected_strategy = (
        "regenerate-all"
        if strategy == "regenerate-all" or (strategy == "reuse-foreign" and output_model == "dataclasses.dataclass")
        else "reuse-all"
    )
    expected_strategy = record.get("expected_strategies", {}).get(strategy, expected_strategy)
    stem = record["name"] + "_" + expected_strategy
    expected_directory = (
        INPUT_GENERIC_EXPECTED / "dataclass" if output_model == "dataclasses.dataclass" else INPUT_GENERIC_EXPECTED
    )
    source_expected = expected_directory / formatter if record.get("formatter_goldens") else expected_directory
    if record.get("pydantic20_goldens") and PYDANTIC_VERSION.split(".")[:2] == ["2", "0"]:
        source_expected = expected_directory / "pydantic20" / formatter
    code_name = (
        "preserved.py"
        if preserved
        else record.get("legacy_code_names", {}).get(
            f"{formatter}_{black.__version__.split('.')[0]}_{expected_strategy}", stem + ".py"
        )
    )
    expected_files = record.get("expected_files", {})
    code_file = (source_expected / code_name).relative_to(INPUT_GENERIC_EXPECTED).as_posix()
    expected_file = INPUT_GENERIC_EXPECTED / expected_files.get(code_file, code_file)
    if entrypoint == "cli":
        extra_args = [
            "--disable-timestamp",
            "--output-model-type",
            output_model,
            *([] if preserved else ["--input-model-ref-strategy", strategy]),
            "--formatters",
            *formatters,
        ]
        if preserved:
            run_main_and_assert(
                input_path=DATA_PATH / "jsonschema/generic_reuse_type.json",
                input_file_type="jsonschema",
                output_path=output,
                expected_file=expected_file,
                extra_args=extra_args,
            )
        else:
            run_input_model_and_assert(
                input_model=paths[0],
                output_path=output,
                expected_file=expected_file,
                extra_args=[*_input_model_args(paths[1:]), *extra_args],
            )
    else:
        schema = (
            DATA_PATH / "jsonschema/generic_reuse_type.json"
            if preserved
            else json.loads(
                json.dumps(
                    load_model_schema(
                        paths,
                        InputFileType.JsonSchema,
                        InputModelRefStrategy(strategy),
                        DataModelType(output_model),
                    )
                )
            )
        )
        run_generate_and_assert(
            input_=schema,
            expected_file=expected_file,
            config=GenerateConfig(
                input_file_type=InputFileType.JsonSchema,
                output_model_type=DataModelType(output_model),
                disable_timestamp=True,
                input_filename=None if preserved else "<stdin>",
                output=output,
                settings_path=tmp_path,
                formatters=[Formatter(value) for value in formatters],
            ),
        )
    with _generated_model(output, "generic_output", record["sources"][0].split(":")[1]):  # noqa: PLR1702
        rows = []
        for source, payload, invalid in zip(record["sources"], record["payloads"], record["invalid"], strict=True):
            module_name, name = source.split(":")
            native = getattr(importlib.import_module("tests.data.python.input_model." + module_name), name)
            generated = getattr(sys.modules["generic_output"], name)
            if output_model == "dataclasses.dataclass":
                _model_json_validator(generated)
            native_fields = getattr(native, "model_fields", None)
            native_annotations = (
                {field: info.annotation for field, info in native_fields.items()}
                if native_fields is not None
                else get_type_hints(native)
            )
            generated_annotations = (
                {field: info.annotation for field, info in generated.model_fields.items()}
                if hasattr(generated, "model_fields")
                else get_type_hints(generated)
            )
            row = {
                "name": name,
                "fields": list(generated_annotations),
                "annotations_equal": {
                    field: generated_annotations[field] == annotation
                    for field, annotation in native_annotations.items()
                },
                "type_identity": {
                    field: generated_annotations[field] is annotation
                    for field, annotation in native_annotations.items()
                    if get_origin(annotation) is None and isinstance(annotation, type)
                },
                "results": {},
            }
            leaf_ids = {}
            for label, annotations in [
                ("native", native_annotations),
                ("generated", generated_annotations),
            ]:
                leaf_ids[label] = {}
                for field, annotation in annotations.items():
                    pending = [annotation]
                    leaves = []
                    while pending:
                        item = pending.pop()
                        if get_origin(item) is None and isinstance(item, type):
                            leaves.append(id(item))
                        else:
                            pending.extend(get_args(item))
                    leaf_ids[label][field] = leaves
            row["type_leaf_identity"] = {
                field: leaves == leaf_ids["generated"][field] for field, leaves in leaf_ids["native"].items()
            }
            for label, model in [("native", native), ("generated", generated)]:
                adapter = TypeAdapter(model)
                try:
                    row["results"][label] = {
                        "valid": adapter.dump_python(adapter.validate_python(payload), mode="json")
                    }
                except ValidationError as error:
                    row["results"][label] = {
                        "valid_error": [(list(item["loc"]), item["type"]) for item in error.errors()]
                    }
                try:
                    adapter.validate_python(invalid)
                except ValidationError as error:
                    row["results"][label]["invalid"] = [(list(item["loc"]), item["type"]) for item in error.errors()]
                else:
                    row["results"][label]["invalid"] = None
                if record.get("extra_invalid"):
                    extra_invalid = []
                    for extra in record["extra_invalid"]:
                        try:
                            adapter.validate_python(extra)
                        except ValidationError as error:  # noqa: PERF203
                            extra_invalid.append([(list(item["loc"]), item["type"]) for item in error.errors()])
                        else:
                            extra_invalid.append(None)
                    row["results"][label]["extra_invalid"] = extra_invalid
            if record.get("instance_fields"):
                native_instance = TypeAdapter(native).validate_python(payload)
                generated_instance = TypeAdapter(generated).validate_python(payload)
                row["instances"] = {}
                for field in record["instance_fields"]:
                    native_value = (
                        native_instance[field] if isinstance(native_instance, dict) else getattr(native_instance, field)
                    )
                    generated_value = getattr(generated_instance, field)
                    row["instances"][field] = {
                        "type_identity": type(generated_value) is type(native_value),
                        "value_identity": generated_value.value is native_value.value,
                    }
            rows.append(row)
    runtime_file = (expected_directory / (stem + ".txt")).relative_to(INPUT_GENERIC_EXPECTED).as_posix()
    assert_output(
        json.dumps(rows, indent=2) + "\n", INPUT_GENERIC_EXPECTED / expected_files.get(runtime_file, runtime_file)
    )


@pytest.mark.parametrize("case", ["name", "family", "index_type", "index_negative"])
def test_python_native_field_invalid_path(case: str, tmp_path: Path) -> None:
    """Reject malformed external native paths before generating executable annotations."""
    record = json.loads((DATA_PATH / "payloads/generic_native_fields" / (case + ".json")).read_text())
    with pytest.raises((TypeError, ValueError), match=record["message"]):
        run_generate_and_assert(
            input_=record["schema"],
            expected_file=tmp_path / "output.py",
            config=GenerateConfig(input_file_type=InputFileType.JsonSchema, output=tmp_path / "output.py"),
        )


@pytest.mark.parametrize("entrypoint", ["cli", "api"])
@pytest.mark.parametrize("formatter", ["builtin", "external"])
@pytest.mark.parametrize("strategy", ["regenerate-all", "reuse-all", "reuse-foreign"])
@pytest.mark.parametrize(
    "case",
    [
        "DataInner",
        "TypedInner",
        "ModelInner",
        "DataRoot",
        "TypedRoot",
        "ModelRoot",
        "DeepRoot",
        "Recursive",
        "SameNames",
    ],
)
def test_nested_family_python_types(tmp_path: Path, entrypoint: str, formatter: str, strategy: str, case: str) -> None:
    """Supplement exact nested definitions without losing native collection behavior."""
    from tests.data.python.input_model import nested_family_types

    output = tmp_path / "output.py"
    (tmp_path / "pyproject.toml").write_text(
        (Path(__file__).parent / "data/python/input_model/collision_settings/pyproject.toml").read_text()
    )
    paths = [f"tests.data.python.input_model.nested_family_types:{case}"]
    formatters = ["builtin"] if formatter == "builtin" else ["black", "isort"]
    if entrypoint == "cli":
        run_main_with_args(
            _input_model_args(
                paths,
                output_path=output,
                extra_args=["--disable-timestamp", "--input-model-ref-strategy", strategy, "--formatters", *formatters],
            )
        )
        assert_output(output.read_text(), EXPECTED_INPUT_MODEL_PATH / f"nested_family_{case}_{strategy}.py")
    else:
        schema = load_model_schema(
            paths, InputFileType.JsonSchema, InputModelRefStrategy(strategy), DataModelType.PydanticV2BaseModel
        )
        run_generate_and_assert(
            input_=schema,
            expected_file=EXPECTED_INPUT_MODEL_PATH / f"nested_family_{case}_{strategy}.py",
            config=GenerateConfig(
                input_file_type=InputFileType.JsonSchema,
                output=output,
                input_filename="<stdin>",
                disable_timestamp=True,
                settings_path=tmp_path,
                formatters=[Formatter(value) for value in formatters],
            ),
        )
    cases = json.loads((Path(__file__).parent / "data/payloads/nested_family_types.json").read_text())
    fixture = next(item for item in cases if item["name"] == case)
    with _generated_model(output, "nested_family_output", case) as model:
        source_type = getattr(nested_family_types, case)
        native = TypeAdapter(source_type).validate_python(fixture["payload"])
        generated = model.model_validate(fixture["payload"])
        for path in fixture["paths"]:
            original, result = native, generated
            for name in path:
                original = original[name] if isinstance(original, dict) else getattr(original, name)
                result = result[name] if isinstance(result, dict) else getattr(result, name)
            original = original["values"] if isinstance(original, dict) else original.values
            result = result["values"] if isinstance(result, dict) else result.values
            assert type(result) is type(original) is frozenset
            assert result == original
            assert hash(result) == hash(original)
        if case == "DeepRoot":
            for name in ("DataInner", "TypedInner", "ModelInner"):
                result = getattr(sys.modules["nested_family_output"], name).model_validate({"values": [1, 2]})
                assert result.values == frozenset({1, 2})
                assert isinstance(result.values, frozenset)


PYTHON_ROOT_EXPECTED = DATA_PATH / "expected/main/input_model/root_models"


@pytest.mark.parametrize("entrypoint", ["cli", "api"])
@pytest.mark.parametrize("input_format", ["module", "file"])
@pytest.mark.parametrize(
    "case",
    json.loads((DATA_PATH / "payloads/input_model_roots.json").read_text()),
    ids=lambda case: f"{case['name']}-{case['strategy']}",
)
def test_python_root_model_inputs(
    tmp_path: Path, entrypoint: str, input_format: str, case: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Keep root values, constraints, defaults and ordinary model inheritance."""
    output_file = tmp_path / "output.py"
    from datamodel_code_generator.format import Formatter

    (output_file.parent / "pyproject.toml").write_bytes(
        (DATA_PATH / "python/input_model/root_models_format/pyproject.toml").read_bytes()
    )
    formatters = [Formatter.BLACK, Formatter.ISORT] if _uses_external_test_default_formatter() else [Formatter.BUILTIN]
    name = case["name"]
    strategy = case["strategy"]
    source = (
        f"tests.data.python.input_model.root_models:{name}"
        if input_format == "module"
        else f"tests/data/python/input_model/root_models.py:{name}"
    )
    expected = PYTHON_ROOT_EXPECTED / case["expected"][input_format]
    if entrypoint == "cli":
        run_main_with_args(
            [
                "--output",
                str(output_file),
                "--input-file-type",
                "jsonschema",
                "--input-model",
                source,
                "--input-model-ref-strategy",
                strategy,
                "--output-model-type",
                "pydantic_v2.BaseModel",
                "--target-python-version",
                "3.10",
                "--custom-file-header",
                "# RootModel input control",
                "--field-constraints",
                "--formatters",
                *(formatter.value for formatter in formatters),
            ],
        )
        assert_output(output_file.read_text(encoding="utf-8"), expected)
    else:
        schema = load_model_schema(
            [source],
            InputFileType.JsonSchema,
            ref_strategy=InputModelRefStrategy(strategy),
            output_model_type=DataModelType.PydanticV2BaseModel,
        )
        run_generate_and_assert(
            input_=schema,
            expected_file=expected,
            input_file_type=InputFileType.JsonSchema,
            output_model_type=DataModelType.PydanticV2BaseModel,
            target_python_version=PythonVersion.PY_310,
            field_constraints=True,
            custom_file_header="# RootModel input control",
            formatters=formatters,
            output=output_file,
        )
    if input_format == "file":
        monkeypatch.syspath_prepend(str(DATA_PATH / "python/input_model"))
    native = getattr(importlib.import_module("tests.data.python.input_model.root_models"), name)
    with _generated_model(output_file, "generated_python_root_input", name) as model:
        value = model.model_validate_json(json.dumps(case["valid"]))
        native_value = native.model_validate_json(json.dumps(case["valid"]))
        result = {"dump": value.model_dump(mode="json"), "rejects_invalid": True}
        with pytest.raises(ValidationError):
            model.model_validate_json(json.dumps(case["invalid"]))
        with pytest.raises(ValidationError):
            native.model_validate_json(json.dumps(case["invalid"]))
        if "default" in case:
            result["default"] = model().model_dump(mode="json")
        result["root_model"] = model.__pydantic_root_model__
        if native.__pydantic_root_model__:
            with pytest.raises(ValidationError):
                model.model_validate({})
            with pytest.raises(ValidationError):
                native.model_validate({})
            result["rejects_object"] = True
        expected_result = PYTHON_ROOT_EXPECTED / case["expected"]["runtime"]
        assert_output(json.dumps(result, indent=2) + "\n", expected_result)
        result["dump"] = native_value.model_dump(mode="json")
        result["root_model"] = native.__pydantic_root_model__
        if "default" in case:
            result["default"] = native().model_dump(mode="json")
        assert_output(json.dumps(result, indent=2) + "\n", expected_result)
