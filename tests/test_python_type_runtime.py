"""Tests for live Python type conversion into immutable expressions."""

from __future__ import annotations

from enum import Enum
from gc import collect
from typing import Annotated, ForwardRef, Generic, Literal, ParamSpec, TypeVar
from weakref import ref

import pytest

from datamodel_code_generator._python_type_annotation import (
    PythonTypeEllipsis,
    PythonTypeName,
    PythonTypeOpaqueText,
    PythonTypeRuntimeSymbol,
    PythonTypeStarred,
    PythonTypeSubscript,
    PythonTypeUnion,
    iter_python_type_expr_names,
    render_python_type_expr,
)
from datamodel_code_generator._python_type_runtime import (
    _IdentityWeakRef,
    _python_type_expr_from_runtime_cached,
    _runtime_origin_expr,
    python_callable_expr_from_runtime_args,
    python_type_expr_from_runtime,
    python_type_expr_from_runtime_full_name,
)

T = TypeVar("T")
P = ParamSpec("P")


class RuntimeBox(Generic[T]):
    """Importable generic used by runtime codec tests."""


class RuntimeStatus(Enum):
    """Importable enum used by Literal codec tests."""

    ACTIVE = "active"


class RuntimeNamespace:
    """Nested runtime generic used by the short-origin policy test."""

    class Box(Generic[T]):
        """A valid nested qualname that must remain structured."""


@pytest.mark.allow_direct_assert
def test_runtime_type_cache_uses_identity_without_retaining_classes() -> None:
    """User equality cannot merge runtime symbols and cached classes remain collectible."""
    _python_type_expr_from_runtime_cached.cache_clear()

    class EqualMeta(type):
        def __eq__(cls, other: object) -> bool:
            return isinstance(other, EqualMeta)

        def __hash__(cls) -> int:
            return 1

    class First(metaclass=EqualMeta):
        pass

    class Second(metaclass=EqualMeta):
        pass

    first_expression = python_type_expr_from_runtime(First)
    second_expression = python_type_expr_from_runtime(Second)
    assert python_type_expr_from_runtime(First) is first_expression

    # The cache must ignore user-defined equality and hash collisions.
    assert First == Second
    assert hash(First) == hash(Second)
    assert first_expression != second_expression
    assert render_python_type_expr(first_expression).endswith(".First")
    assert render_python_type_expr(second_expression).endswith(".Second")

    class Ephemeral:
        pass

    ephemeral_ref = ref(Ephemeral)
    python_type_expr_from_runtime(Ephemeral)
    del Ephemeral
    collect()

    assert ephemeral_ref() is None

    cached_types = [type(f"Cached{index}", (), {}) for index in range(257)]
    for cached_type in cached_types:
        python_type_expr_from_runtime(cached_type)

    assert _python_type_expr_from_runtime_cached.cache_info().hits == 1
    assert _python_type_expr_from_runtime_cached.cache_info().currsize == 256
    assert _python_type_expr_from_runtime_cached.cache_parameters() == {"maxsize": 256, "typed": False}
    _python_type_expr_from_runtime_cached.cache_clear()


@pytest.mark.allow_direct_assert
def test_runtime_python_type_codec_builds_structure_without_text_reparse() -> None:
    """Runtime generics, Callable shapes, Literal values, and symbols stay structural."""
    from collections.abc import Callable

    expression = python_type_expr_from_runtime(
        RuntimeBox[Callable[[int, str], Literal[RuntimeStatus.ACTIVE, b"bytes", True, None]]]  # noqa: PYI061
    )

    assert isinstance(expression, PythonTypeSubscript)
    assert isinstance(expression.base, PythonTypeRuntimeSymbol)
    assert render_python_type_expr(expression) == (
        "tests.test_python_type_runtime.RuntimeBox["
        "Callable[[int, str], Literal[RuntimeStatus.ACTIVE, b'bytes', True, None]]]"
    )
    assert render_python_type_expr(python_type_expr_from_runtime_full_name(RuntimeBox[int])) == "RuntimeBox[int]"


@pytest.mark.allow_direct_assert
def test_runtime_python_type_codec_handles_forward_refs_annotated_and_callable_forms() -> None:
    """Boundary-only opaque text and all Callable parameter shapes remain explicit."""
    from collections.abc import Callable

    assert python_type_expr_from_runtime(Annotated[int, []]) == PythonTypeName("int")
    assert python_type_expr_from_runtime_full_name(ForwardRef("future.Model")) == PythonTypeOpaqueText("future.Model")
    assert render_python_type_expr(python_type_expr_from_runtime(Callable[..., None])) == "Callable[..., None]"
    assert render_python_type_expr(python_type_expr_from_runtime(Callable[[], None])) == "Callable[[], None]"
    assert python_callable_expr_from_runtime_args(()) == PythonTypeName("Callable")
    assert render_python_type_expr(python_callable_expr_from_runtime_args((int, bool))) == "Callable[[int], bool]"
    assert python_type_expr_from_runtime(...) == PythonTypeEllipsis()
    assert python_type_expr_from_runtime(type(None)) == PythonTypeName("None")
    assert python_type_expr_from_runtime(int | str) == PythonTypeUnion((PythonTypeName("int"), PythonTypeName("str")))
    assert render_python_type_expr(python_type_expr_from_runtime(type[int])) == "Type[int]"


@pytest.mark.allow_direct_assert
def test_runtime_codec_covers_fallbacks_paramspec_and_full_name_policies() -> None:
    """Runtime-only edge shapes remain structured without target-grammar inference."""
    from collections.abc import Callable
    from types import SimpleNamespace

    invalid_module = SimpleNamespace(__module__="bad-module", __qualname__="Model")
    invalid_name = SimpleNamespace(__module__="", __qualname__="bad-name")
    nameless = SimpleNamespace()

    class NamelessOrigin:
        def __str__(self) -> str:
            return "typing.Opaque"

    assert python_type_expr_from_runtime(invalid_module) == PythonTypeOpaqueText("bad-module.Model")
    assert python_type_expr_from_runtime(invalid_name) == PythonTypeOpaqueText("bad-name")
    assert isinstance(python_type_expr_from_runtime(nameless), PythonTypeOpaqueText)
    assert python_type_expr_from_runtime(ForwardRef("future.Model")) == PythonTypeOpaqueText("future.Model")
    assert _runtime_origin_expr(NamelessOrigin(), qualify=True) == PythonTypeName("Opaque")
    assert render_python_type_expr(python_callable_expr_from_runtime_args((P, int))) == (
        "Callable[tests.test_python_type_runtime.P, int]"
    )
    assert render_python_type_expr(python_callable_expr_from_runtime_args((int, str, bool))) == (
        "Callable[[int, str], bool]"
    )
    assert render_python_type_expr(python_type_expr_from_runtime_full_name(Callable[[int], bool])) == (
        "Callable[[int], bool]"
    )
    assert python_type_expr_from_runtime_full_name(Annotated[int, "metadata"]) == PythonTypeName("int")
    assert render_python_type_expr(python_type_expr_from_runtime_full_name(Literal["value"])) == "Literal['value']"
    assert render_python_type_expr(python_type_expr_from_runtime_full_name(type[RuntimeBox])) == (
        "Type[tests.test_python_type_runtime.RuntimeBox]"
    )
    nested = python_type_expr_from_runtime_full_name(RuntimeNamespace.Box[int])
    assert isinstance(nested, PythonTypeSubscript)
    assert nested.base == PythonTypeRuntimeSymbol("", ("RuntimeNamespace", "Box"))
    assert render_python_type_expr(nested) == "RuntimeNamespace.Box[int]"
    assert python_type_expr_from_runtime_full_name("Model") == PythonTypeOpaqueText("Model")
    assert python_type_expr_from_runtime_full_name(type(None)) == PythonTypeName("None")
    assert python_type_expr_from_runtime_full_name(int | str) == PythonTypeUnion((
        PythonTypeName("int"),
        PythonTypeName("str"),
    ))
    assert list(iter_python_type_expr_names(PythonTypeStarred(PythonTypeName("Ts")))) == ["Ts"]
    identity_ref = _IdentityWeakRef(RuntimeBox)
    identical_object = identity_ref
    assert identity_ref == identical_object
    same_identity_ref = _IdentityWeakRef(RuntimeBox)
    assert identity_ref == same_identity_ref
