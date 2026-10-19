"""Generate and import payload models for validation tests."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import sys
import warnings
from dataclasses import dataclass, is_dataclass
from typing import TYPE_CHECKING, Any

import pytest
from packaging.version import Version
from pydantic import VERSION as PYDANTIC_VERSION
from pydantic import TypeAdapter, ValidationError

from datamodel_code_generator import enable_parsed_source_cache
from datamodel_code_generator.__main__ import Exit, main
from tests.conftest import assert_output
from tests.main.conftest import EXPECTED_MAIN_PATH

from .constants import (
    PAYLOAD_CLASS_NAME,
    PAYLOAD_CODEGEN_WARNING_FILES,
    PAYLOAD_RUNS_UNSET_TARGET_PYDANTIC_VERSION,
    PAYLOAD_TARGET_PYDANTIC_VERSION,
    PAYLOAD_TARGET_PYTHON_VERSION,
)
from .models import PayloadBackend

if TYPE_CHECKING:
    from pathlib import Path

    from .models import SchemaCase


_PYDANTIC_FLOOR_STATEMENT = re.compile(r"The generated models need pydantic>=(?P<minimum>\d+(?:\.\d+)*)")
"""The statement of the Pydantic release that generated models need above the target, with that release."""


def _stated_floor(recorded: list[warnings.WarningMessage]) -> str | None:
    """Return the release that the recorded statement of a generation names, or None."""
    return next(
        (match["minimum"] for record in recorded if (match := _PYDANTIC_FLOOR_STATEMENT.match(str(record.message)))),
        None,
    )


def _safe_filename(case_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", case_id)


def _write_input_schema(case: SchemaCase, directory: Path) -> Path:
    input_path = directory / f"input{case.temp_input_suffix}"
    if case.temp_input_suffix == ".json":
        input_path.write_text(json.dumps(case.codegen_schema), encoding="utf-8")
    else:
        import yaml

        input_path.write_text(yaml.safe_dump(case.codegen_schema, sort_keys=False), encoding="utf-8")
    return input_path


class PayloadAdapterError(Exception):
    """Raised when a generated payload adapter cannot be created."""


@dataclass(frozen=True)
class PayloadRuntime:
    """Backend-specific runtime validator for a generated payload type."""

    backend: PayloadBackend
    payload_type: Any
    adapter: TypeAdapter[Any] | None = None

    @property
    def rejection_exceptions(self) -> tuple[type[Exception], ...]:
        """Return exceptions that mean backend validation rejected a payload."""
        match self.backend:
            case PayloadBackend.PYDANTIC_V2 | PayloadBackend.PYDANTIC_V2_DATACLASS:
                return (ValidationError,)
            case PayloadBackend.MSGSPEC:
                import msgspec

                return (msgspec.ValidationError,)
            case PayloadBackend.DATACLASSES:
                return (TypeError,)
            case _:
                msg = f"Unsupported payload backend: {self.backend!r}"
                raise PayloadAdapterError(msg)

    def is_rejection_exception(self, exception: Exception) -> bool:
        """Return whether an exception means backend validation rejected a payload."""
        if isinstance(exception, self.rejection_exceptions):
            return True

        if self.backend is PayloadBackend.MSGSPEC:
            # Python 3.14 CI has observed msgspec.ValidationError from a different module instance.
            exception_type = type(exception)
            return exception_type.__module__ == "msgspec" and exception_type.__name__ == "ValidationError"

        return False

    def assert_rejects_python(self, payload: Any) -> None:
        """Assert the backend rejects a Python payload."""
        try:
            self.validate_python(payload)
        except Exception as exception:
            if self.is_rejection_exception(exception):
                return
            raise

        pytest.fail(f"{self.backend.value} runtime accepted an invalid payload")

    def validate_python(self, payload: Any) -> Any:
        """Validate or construct the payload using the generated backend."""
        match self.backend:
            case PayloadBackend.PYDANTIC_V2 | PayloadBackend.PYDANTIC_V2_DATACLASS:
                if self.adapter is None:  # pragma: no cover
                    msg = f"{self.backend.value} runtime is missing a TypeAdapter"
                    raise PayloadAdapterError(msg)
                return self.adapter.validate_python(payload)
            case PayloadBackend.MSGSPEC:
                import msgspec

                return msgspec.convert(payload, type=self.payload_type)
            case PayloadBackend.DATACLASSES:
                return _construct_dataclass_payload(self.payload_type, payload)
            case _:
                msg = f"Unsupported payload backend: {self.backend!r}"
                raise PayloadAdapterError(msg)


def _construct_dataclass_payload(payload_type: Any, payload: Any) -> Any:
    if not is_dataclass(payload_type):
        return payload
    if isinstance(payload, dict):
        return payload_type(**payload)
    return payload_type(payload)


def _payload_codegen_args(case: SchemaCase, input_path: Path, output_path: Path, backend: PayloadBackend) -> list[str]:
    args = [
        "--input",
        str(input_path),
        "--input-file-type",
        case.input_file_type,
        "--output",
        str(output_path),
        "--output-model-type",
        backend.output_model_type,
        "--target-python-version",
        PAYLOAD_TARGET_PYTHON_VERSION,
        "--class-name",
        PAYLOAD_CLASS_NAME,
        "--disable-timestamp",
        "--formatters",
        "builtin",
    ]
    if case.input_file_type == "openapi":
        args.extend(["--openapi-scopes", "schemas", "--strict-nullable"])
    if not PAYLOAD_RUNS_UNSET_TARGET_PYDANTIC_VERSION and backend in {
        PayloadBackend.PYDANTIC_V2,
        PayloadBackend.PYDANTIC_V2_DATACLASS,
    }:
        args.extend(["--target-pydantic-version", PAYLOAD_TARGET_PYDANTIC_VERSION])
    args.extend(case.backend_extra_args.get(backend, ()))
    return args


def _run_payload_codegen(args: list[str]) -> Exit:
    restore = enable_parsed_source_cache()
    try:
        return main(args)
    finally:
        restore()


def _load_payload_type(module_name: str, output_path: Path) -> Any:
    """Load the named alias, including None, or the sole generated class."""
    spec = importlib.util.spec_from_file_location(module_name, output_path)
    if spec is None or spec.loader is None:
        msg = f"Unable to import generated module from {output_path}"
        raise PayloadAdapterError(msg)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        msg = f"Generated module failed to import: {type(exc).__name__}: {exc}"
        raise PayloadAdapterError(msg) from exc
    if PAYLOAD_CLASS_NAME in module.__dict__:
        return module.__dict__[PAYLOAD_CLASS_NAME]
    generated_types = [
        value
        for value in module.__dict__.values()
        if isinstance(value, type) and getattr(value, "__module__", None) == module_name
    ]
    match len(generated_types):
        case 0:
            msg = f"Generated module did not contain {PAYLOAD_CLASS_NAME}"
        case 1:
            return generated_types[0]
        case generated_type_count:
            generated_type_names = ", ".join(sorted(generated_type.__name__ for generated_type in generated_types))
            msg = (
                f"Generated module contained {generated_type_count} generated types instead of "
                f"{PAYLOAD_CLASS_NAME}: {generated_type_names}"
            )
    raise PayloadAdapterError(msg)


def _payload_runtime(payload_type: Any, backend: PayloadBackend) -> PayloadRuntime:
    match backend:
        case PayloadBackend.PYDANTIC_V2 | PayloadBackend.PYDANTIC_V2_DATACLASS:
            try:
                adapter = TypeAdapter(payload_type)
            except Exception as exc:
                msg = f"Generated payload type could not be adapted: {type(exc).__name__}: {exc}"
                raise PayloadAdapterError(msg) from exc
            return PayloadRuntime(backend=backend, payload_type=payload_type, adapter=adapter)
        case PayloadBackend.MSGSPEC | PayloadBackend.DATACLASSES:
            return PayloadRuntime(backend=backend, payload_type=payload_type)
        case _:
            msg = f"Unsupported payload backend: {backend!r}"
            raise PayloadAdapterError(msg)


def _assert_codegen_warnings(messages: list[str], warning_file: str) -> None:
    """Compare the warnings of a case's generation, other than the floor statement, with its warning file.

    Each test that loads the case compares them, also when an earlier test of the session generated it.
    """
    assert_output(
        json.dumps([message for message in messages if not _PYDANTIC_FLOOR_STATEMENT.match(message)], indent=2) + "\n",
        EXPECTED_MAIN_PATH / warning_file,
    )


def generate_payload_runtime(
    case: SchemaCase,
    generated_model_cache: dict[str, Any],
    backend: PayloadBackend,
) -> PayloadRuntime:
    """Generate or load the backend runtime for a payload validation case.

    The generation's statement of the Pydantic release that the models need above the payload target is kept for
    ``runtime_below_stated_floor``, not raised.
    """
    runtimes: dict[tuple[str, str], PayloadRuntime] = generated_model_cache["adapters"]
    cache_key = (backend.value, case.id)
    warning_file = PAYLOAD_CODEGEN_WARNING_FILES.get(case.id)
    if (runtime := runtimes.get(cache_key)) is not None:
        if warning_file:
            _assert_codegen_warnings(generated_model_cache["warnings"][cache_key], warning_file)
        return runtime

    case_dir = generated_model_cache["base"] / _safe_filename(backend.value) / _safe_filename(case.id)
    case_dir.mkdir(parents=True, exist_ok=True)
    input_path = _write_input_schema(case, case_dir)
    output_path = case_dir / "model.py"
    with warnings.catch_warnings(record=True) as captured:
        if warning_file:
            warnings.simplefilter("always", UserWarning)
        else:
            warnings.filterwarnings("ignore", r"The schema at .* admits (only its const|no value)", UserWarning)
        warnings.filterwarnings("always", _PYDANTIC_FLOOR_STATEMENT.pattern, UserWarning)
        return_code = _run_payload_codegen(_payload_codegen_args(case, input_path, output_path, backend))
    generated_model_cache.setdefault("floors", {})[cache_key] = _stated_floor(captured)
    if warning_file:
        messages = [str(record.message) for record in captured]
        generated_model_cache.setdefault("warnings", {})[cache_key] = messages
        _assert_codegen_warnings(messages, warning_file)
    if return_code != Exit.OK:
        msg = f"Generation failed with exit code {return_code!r}"
        raise PayloadAdapterError(msg)
    module_digest = hashlib.sha256("\0".join(cache_key).encode()).hexdigest()
    module_name = f"payload_validation_{module_digest}"
    payload_type = _load_payload_type(module_name, output_path)
    runtime = _payload_runtime(payload_type, backend)
    runtimes[cache_key] = runtime
    return runtime


def generate_payload_adapter(
    case: SchemaCase, generated_model_cache: dict[str, Any], backend: PayloadBackend = PayloadBackend.PYDANTIC_V2
) -> TypeAdapter[Any]:
    """Generate or load the Pydantic v2 adapter for a payload validation case."""
    runtime = generate_payload_runtime(case, generated_model_cache, backend)
    if runtime.adapter is None:  # pragma: no cover
        msg = f"{case.id}: pydantic v2 backend did not create a TypeAdapter"
        raise PayloadAdapterError(msg)
    return runtime.adapter


def runtime_below_stated_floor(
    case: SchemaCase, generated_model_cache: dict[str, Any], backend: PayloadBackend
) -> str | None:
    """Return the release that the case's generation stated its models need when the installed Pydantic is older."""
    floor = generated_model_cache.get("floors", {}).get((backend.value, case.id))
    return floor if floor is not None and Version(PYDANTIC_VERSION) < Version(floor) else None


def stated_pydantic_floor(case: SchemaCase, directory: Path, backend: PayloadBackend, *args: str) -> str | None:
    """Generate the case with the arguments added and return the Pydantic release its generation states, or None."""
    input_path = _write_input_schema(case, directory)
    with warnings.catch_warnings(record=True) as captured:
        warnings.filterwarnings("always", _PYDANTIC_FLOOR_STATEMENT.pattern, UserWarning)
        _run_payload_codegen([*_payload_codegen_args(case, input_path, directory / "model.py", backend), *args])
    return _stated_floor(captured)


def _skip_below_stated_floor(case: SchemaCase, generated_model_cache: dict[str, Any], backend: PayloadBackend) -> None:
    """Skip a case whose generation states that its models need a newer Pydantic than the installed one."""
    if (floor := runtime_below_stated_floor(case, generated_model_cache, backend)) is not None:
        pytest.skip(f"{case.id} [{backend.value}]: the generated models need pydantic>={floor}")


def load_generated_payload_runtime(
    case: SchemaCase,
    generated_model_cache: dict[str, Any],
    backend: PayloadBackend,
) -> PayloadRuntime:
    """Generate or load the runtime validator for a payload validation case, skipping it below its stated floor."""
    try:
        runtime = generate_payload_runtime(case, generated_model_cache, backend)
    except PayloadAdapterError as exc:  # pragma: no cover
        _skip_below_stated_floor(case, generated_model_cache, backend)
        pytest.fail(f"{case.id} [{backend.value}]: {exc}")
        raise AssertionError from exc
    _skip_below_stated_floor(case, generated_model_cache, backend)
    return runtime


def load_generated_payload_adapter(
    case: SchemaCase, generated_model_cache: dict[str, Any], backend: PayloadBackend = PayloadBackend.PYDANTIC_V2
) -> TypeAdapter[Any]:
    """Generate or load the Pydantic v2 adapter for a payload validation case, skipping it below its stated floor."""
    try:
        adapter = generate_payload_adapter(case, generated_model_cache, backend)
    except PayloadAdapterError as exc:  # pragma: no cover
        _skip_below_stated_floor(case, generated_model_cache, backend)
        pytest.fail(f"{case.id}: {exc}")
        raise AssertionError from exc
    _skip_below_stated_floor(case, generated_model_cache, backend)
    return adapter
