"""Tests for target-stable Python type import metadata."""

from __future__ import annotations

import pickle

import pytest

from datamodel_code_generator._python_type_import_registry import (
    PythonTypeUnavailableError,
    get_python_type_import_path,
    get_qualified_python_type_import_path,
    is_python_builtin_type_name,
)


@pytest.mark.allow_direct_assert
def test_target_stable_python_type_import_lookup_is_lazy_and_bounded() -> None:
    """Static lookup is target-aware and bounds user-controlled unknown leaves."""
    get_python_type_import_path.cache_clear()

    assert get_python_type_import_path("Callable", (3, 10)) == "collections.abc.Callable"
    assert get_python_type_import_path("Never", (3, 10)) == "typing_extensions.Never"
    assert get_python_type_import_path("Never", (3, 11)) == "typing.Never"
    assert get_python_type_import_path("PurePosixPath", (3, 10)) == "pathlib.PurePosixPath"
    assert get_python_type_import_path("DecimalException", (3, 10)) == "decimal.DecimalException"
    assert get_python_type_import_path("auto", (3, 10)) == "enum.auto"
    assert get_python_type_import_path("property", (3, 11)) == "enum.property"
    with pytest.raises(PythonTypeUnavailableError) as exc_info:
        get_python_type_import_path("PathInfo", (3, 10))
    assert exc_info.value.type_name == "PathInfo"
    assert exc_info.value.target_version == (3, 10)
    assert str(exc_info.value) == "PathInfo is unavailable for target Python 3.10"
    unpickled = pickle.loads(pickle.dumps(exc_info.value))
    assert (unpickled.type_name, unpickled.target_version, str(unpickled)) == (
        "PathInfo",
        (3, 10),
        "PathInfo is unavailable for target Python 3.10",
    )
    assert get_python_type_import_path("PathInfo", (3, 14)) == "pathlib.types.PathInfo"
    with pytest.raises(PythonTypeUnavailableError):
        get_python_type_import_path("StrEnum", (3, 10))
    with pytest.raises(PythonTypeUnavailableError):
        get_python_type_import_path("property", (3, 10))
    assert get_python_type_import_path("UnknownExternalType", (3, 14)) is None
    assert get_python_type_import_path.cache_info().currsize == 9
    assert get_python_type_import_path.cache_parameters() == {"maxsize": 256, "typed": False}


@pytest.mark.parametrize(
    ("type_name", "expected_path"),
    [
        ("ByteString", "collections.abc.ByteString"),
        ("Container", "collections.abc.Container"),
        ("Hashable", "collections.abc.Hashable"),
        ("ItemsView", "collections.abc.ItemsView"),
        ("KeysView", "collections.abc.KeysView"),
        ("MappingView", "collections.abc.MappingView"),
        ("Sized", "collections.abc.Sized"),
        ("ValuesView", "collections.abc.ValuesView"),
        ("AbstractSet", "typing.AbstractSet"),
        ("AsyncContextManager", "typing.AsyncContextManager"),
        ("ContextManager", "typing.ContextManager"),
        ("DefaultDict", "typing.DefaultDict"),
        ("Deque", "typing.Deque"),
        ("UserDict", "collections.UserDict"),
    ],
)
@pytest.mark.allow_direct_assert
def test_ambiguous_collection_types_use_real_runtime_modules(type_name: str, expected_path: str) -> None:
    """Prefer real ABCs without inventing collections.abc aliases."""
    assert get_python_type_import_path(type_name, (3, 10)) == expected_path


@pytest.mark.allow_direct_assert
def test_qualified_python_type_import_lookup_preserves_explicit_modules() -> None:
    """Gate known stdlib paths while leaving custom modules and valid choices intact."""
    get_qualified_python_type_import_path.cache_clear()

    assert get_qualified_python_type_import_path("typing.TypeIs", (3, 10)) == "typing_extensions.TypeIs"
    assert get_qualified_python_type_import_path("typing.Callable", (3, 10)) == "typing.Callable"
    assert get_qualified_python_type_import_path("foo.TypeIs", (3, 10)) == "foo.TypeIs"
    assert get_qualified_python_type_import_path("TypeIs", (3, 10)) == "TypeIs"
    assert get_qualified_python_type_import_path("enum.property", (3, 11)) == "enum.property"
    assert get_qualified_python_type_import_path("builtins.ExceptionGroup", (3, 11)) == "builtins.ExceptionGroup"
    with pytest.raises(PythonTypeUnavailableError, match=r"enum\.StrEnum"):
        get_qualified_python_type_import_path("enum.StrEnum", (3, 10))
    with pytest.raises(PythonTypeUnavailableError, match=r"enum\.property"):
        get_qualified_python_type_import_path("enum.property", (3, 10))
    with pytest.raises(PythonTypeUnavailableError, match=r"builtins\.ExceptionGroup"):
        get_qualified_python_type_import_path("builtins.ExceptionGroup", (3, 10))
    assert get_qualified_python_type_import_path.cache_info().currsize == 6
    assert get_qualified_python_type_import_path.cache_parameters() == {"maxsize": 256, "typed": False}


@pytest.mark.allow_direct_assert
def test_target_stable_builtin_type_lookup_is_complete_and_version_gated() -> None:
    """Recognize public builtin classes without consulting the host runtime."""
    assert is_python_builtin_type_name("property", (3, 10))
    assert is_python_builtin_type_name("ExceptionGroup", (3, 11))
    assert not is_python_builtin_type_name("ExceptionGroup", (3, 10))
    assert is_python_builtin_type_name("ValueError", (3, 10))
    assert not is_python_builtin_type_name("ExternalType", (3, 14))
    assert not is_python_builtin_type_name("PythonFinalizationError", (3, 12))
