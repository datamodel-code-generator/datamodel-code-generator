"""Tests for immutable Python type expressions and their text codec."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import FrozenInstanceError
from math import inf, nan
from typing import TYPE_CHECKING

import pytest

from datamodel_code_generator._python_type_annotation import (
    PythonTypeBoundName,
    PythonTypeEllipsis,
    PythonTypeExpr,
    PythonTypeLiteralValue,
    PythonTypeMember,
    PythonTypeName,
    PythonTypeOpaqueText,
    PythonTypeParameterList,
    PythonTypeQualifiedName,
    PythonTypeRuntimeSymbol,
    PythonTypeStarred,
    PythonTypeSubscript,
    PythonTypeTuple,
    PythonTypeUnion,
    iter_python_type_expr_names,
    iter_python_type_expr_qualified_names,
    parse_python_type_annotation,
    python_type_expr_arguments,
    python_type_expr_base_name,
    python_type_name,
    render_python_type_expr,
    rewrite_python_type_expr,
)
from datamodel_code_generator._python_type_binding import (
    BoundPythonType,
    alias_bound_python_type,
    python_type_import_name,
)
from datamodel_code_generator.imports import Import, Imports
from datamodel_code_generator.types import is_python_type_annotation

if TYPE_CHECKING:
    from collections.abc import Callable


def test_python_type_expr_is_frozen_slotted_and_shared_by_deepcopy() -> None:
    """Expressions do not own mutable dictionaries or duplicate during copying."""
    expression = PythonTypeSubscript(PythonTypeName("list"), (PythonTypeName("str"),))

    assert not hasattr(expression, "__dict__")
    assert deepcopy(expression) is expression
    field_name = "base"
    with pytest.raises(FrozenInstanceError):
        setattr(expression, field_name, PythonTypeName("tuple"))


@pytest.mark.parametrize(
    "factory",
    [
        lambda: PythonTypeName("list[str]"),
        lambda: PythonTypeName("class"),
        lambda: PythonTypeQualifiedName(()),
        lambda: PythonTypeQualifiedName(("foo", "class")),
        lambda: PythonTypeRuntimeSymbol("bad-module", ("Model",)),
        lambda: PythonTypeRuntimeSymbol("models", ("Outer", "<locals>")),
        lambda: PythonTypeBoundName("bad-name", "foo", "Bar"),
        lambda: PythonTypeBoundName("Bar", "foo", ""),
        lambda: PythonTypeMember(PythonTypeName("HTTPStatus"), "bad-name"),
        lambda: PythonTypeLiteralValue(object()),
        lambda: PythonTypeLiteralValue(nan),
    ],
)
def test_python_type_expr_rejects_invalid_values(factory: Callable[[], object]) -> None:
    """Names, runtime identities, and literal values validate at construction."""
    with pytest.raises(ValueError, match=r"Python|literal"):
        factory()


def test_python_type_expr_accepts_none_and_unqualified_runtime_symbols() -> None:
    """None and runtime symbols without a module retain their intended spelling."""
    assert PythonTypeName("None").value == "None"
    assert render_python_type_expr(PythonTypeLiteralValue(None)) == "None"
    assert render_python_type_expr(PythonTypeRuntimeSymbol("", ("Model",))) == "Model"


def test_runtime_symbol_alias_preserves_dotted_import_identity() -> None:
    """A direct dotted import and its runtime symbol receive the same alias."""
    import_ = Import(import_="pkg.models")
    aliased_import = Import(import_="pkg.models", alias="pkg_1")
    bound_type = BoundPythonType(
        PythonTypeTuple((
            PythonTypeRuntimeSymbol("pkg.models", ("Outer", "Inner")),
            PythonTypeRuntimeSymbol("other", ("Model",)),
        )),
        (import_,),
    )

    aliased_type = alias_bound_python_type(bound_type, {(None, "pkg.models"): aliased_import})
    imports = Imports()
    imports.append(aliased_type.imports)

    assert aliased_type.expression == PythonTypeTuple((
        PythonTypeRuntimeSymbol("pkg_1", ("Outer", "Inner")),
        PythonTypeRuntimeSymbol("other", ("Model",)),
    ))
    assert render_python_type_expr(aliased_type.expression) == "(pkg_1.Outer.Inner, other.Model)"
    assert str(imports) == "import pkg.models as pkg_1"


def test_bound_name_alias_preserves_unbound_same_spelling_leaf() -> None:
    """Only the leaf carrying the matching import identity is renamed."""
    import_ = Import(from_="foo", import_="Bar")
    aliased_import = Import(from_="foo", import_="Bar", alias="Bar_1")
    bound_type = BoundPythonType(
        PythonTypeSubscript(
            PythonTypeName("tuple"),
            (PythonTypeBoundName("Bar", "foo", "Bar"), PythonTypeName("Bar")),
        ),
        (import_,),
    )

    aliased_type = alias_bound_python_type(bound_type, {("foo", "Bar"): aliased_import})

    assert render_python_type_expr(aliased_type.expression) == "tuple[Bar_1, Bar]"
    assert aliased_type.imports == (aliased_import,)


def test_bound_type_alias_noop_shares_immutable_binding() -> None:
    """A no-op alias pass returns the existing immutable binding."""
    bound_type = BoundPythonType(PythonTypeName("str"), ())

    assert alias_bound_python_type(bound_type, {}) is bound_type


def test_bound_type_alias_keeps_unmodified_imports_and_reports_bound_names() -> None:
    """A partial alias rewrite retains imports without matching identities."""
    first_import = Import(from_="first", import_="Thing")
    second_import = Import(from_="second", import_="Other")
    bound_type = BoundPythonType(
        PythonTypeTuple((
            PythonTypeBoundName("Thing", "first", "Thing"),
            PythonTypeBoundName("Other", "second", "Other"),
        )),
        (first_import, second_import),
    )
    aliased_import = Import(from_="first", import_="Thing", alias="Thing_1")

    aliased = alias_bound_python_type(bound_type, {("first", "Thing"): aliased_import})

    assert aliased.imports == (aliased_import, second_import)
    assert python_type_import_name(aliased_import) == "Thing_1"
    assert python_type_import_name(Import(import_="pkg.models")) == "pkg"


@pytest.mark.parametrize(
    ("left", "right"),
    [
        (True, 1),
        (False, 0),
        (1, 1.0),
    ],
)
def test_python_type_literal_value_equality_and_hash_include_value_type(left: object, right: object) -> None:
    """Numerically equal literals of different Python types remain semantically distinct."""
    left_expression = PythonTypeLiteralValue(left)
    right_expression = PythonTypeLiteralValue(right)

    assert left_expression != right_expression
    assert len({left_expression, right_expression}) == 2


def test_python_type_literal_value_equal_values_have_equal_hashes() -> None:
    """Equal typed literal nodes retain the normal equality/hash contract."""
    first = PythonTypeLiteralValue(1)
    second = PythonTypeLiteralValue(1)

    assert first == second
    assert hash(first) == hash(second)
    assert first != object()


def test_parse_python_type_annotation_builds_semantic_structure() -> None:
    """A complex annotation becomes nested semantic nodes and renders stably."""
    expression = parse_python_type_annotation(
        "Callable[[foo.Bar, tuple[int, ...]], dict[str, Literal['x', b'y', -1, +2.5, True, None]]] | None"
    )

    assert isinstance(expression, PythonTypeUnion)
    assert len(expression.items) == 2
    callable_expression = expression.items[0]
    assert isinstance(callable_expression, PythonTypeSubscript)
    assert callable_expression.base is parse_python_type_annotation("Callable")
    assert isinstance(callable_expression.arguments[0], PythonTypeParameterList)
    assert render_python_type_expr(expression) == (
        "Callable[[foo.Bar, tuple[int, ...]], dict[str, Literal['x', b'y', -1, 2.5, True, None]]] | None"
    )


@pytest.mark.parametrize(
    "annotation",
    [
        "str",
        "None",
        "foo.bar.Model",
        "list[str]",
        "Callable[[int, str], bool]",
        "Callable[[(int, str)], bool]",
        "Callable[..., None]",
        "Literal['value', b'bytes', -1, 2.5, True, None]",
        "tuple[(int,)]",
        "tuple[()]",
        "tuple[*Ts]",
        "tuple[*tuple[int, ...]]",
        "Callable[[int, *Ts], bool]",
        "Generic[*Ts, int]",
        "Generic[__datamodel_code_generator_starred, *Ts]",
        "str | int | None",
    ],
)
def test_parse_python_type_annotation_accepts_supported_grammar(annotation: str) -> None:
    """The codec accepts the supported annotation expression grammar."""
    assert parse_python_type_annotation(annotation) is not None
    assert is_python_type_annotation(annotation)


@pytest.mark.parametrize(
    "annotation",
    [
        "",
        "list[",
        "list]",
        "tuple[*Ts",
        "'Model'",
        "...",
        "foo()",
        "foo().bar",
        "list[foo()]",
        "list[{1: 2}]",
        "Generic[*Ts, {str: int}]",
        "Generic[*Ts, foo()]",
        "list[-True]",
        "str + int",
        "lambda: str",
    ],
)
def test_parse_python_type_annotation_rejects_unsupported_grammar(annotation: str) -> None:
    """The codec rejects executable and non-annotation expression forms."""
    assert parse_python_type_annotation(annotation) is None
    assert not is_python_type_annotation(annotation)


def test_parse_python_type_annotation_flattens_both_union_sides() -> None:
    """Nested bit-or syntax is normalized to one union item tuple."""
    expression = parse_python_type_annotation("a | b | c | (d | e)")

    assert isinstance(expression, PythonTypeUnion)
    assert expression.items == tuple(PythonTypeName(name) for name in "abcde")


def test_parse_python_type_annotation_flattens_long_union_without_recursive_conversion() -> None:
    """Normal left-associative union input is flattened in linear conversion work."""
    annotation = " | ".join(f"T{index}" for index in range(900))

    expression = parse_python_type_annotation(annotation)

    assert isinstance(expression, PythonTypeUnion)
    assert len(expression.items) == 900
    assert expression.items[0] == PythonTypeName("T0")
    assert expression.items[-1] == PythonTypeName("T899")


@pytest.mark.parametrize("error", [UnicodeError("invalid Unicode"), RecursionError("too deep")])
def test_parse_python_type_annotation_handles_external_parser_errors(
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
) -> None:
    """Resource and Unicode parser failures are invalid input at the external boundary."""
    from datamodel_code_generator import _python_type_annotation_codec

    parse_python_type_annotation.cache_clear()
    monkeypatch.setattr(
        _python_type_annotation_codec,
        "_parse_python_type_annotation_ast",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(error),
    )

    assert parse_python_type_annotation("External") is None
    parse_python_type_annotation.cache_clear()


def test_parse_python_type_annotation_handles_real_unicode_error() -> None:
    """An unpaired surrogate cannot escape from the raw-text parser boundary."""
    assert parse_python_type_annotation("Model\ud800") is None


def test_parse_python_type_annotation_handles_null_byte() -> None:
    """A raw null byte is invalid input rather than a leaked AST ValueError."""
    assert parse_python_type_annotation("Model\x00") is None


def test_parse_python_type_annotation_interns_common_names() -> None:
    """Frequently repeated names share fixed process-owned leaf nodes."""
    expression = parse_python_type_annotation("list[list[str]]")

    assert isinstance(expression, PythonTypeSubscript)
    nested = expression.arguments[0]
    assert isinstance(nested, PythonTypeSubscript)
    assert expression.base is nested.base
    assert expression.base is parse_python_type_annotation("list")
    assert expression.base is python_type_name("list")


def test_parse_python_type_annotation_uses_bounded_cache() -> None:
    """External text parsing is cached without unbounded string retention."""
    parse_python_type_annotation.cache_clear()

    first = parse_python_type_annotation("dict[str, foo.Bar]")
    second = parse_python_type_annotation("dict[str, foo.Bar]")

    assert second is first
    assert parse_python_type_annotation.cache_info().hits == 1
    assert parse_python_type_annotation.cache_info().misses == 1
    assert parse_python_type_annotation.cache_parameters() == {"maxsize": 1024, "typed": False}


def test_render_python_type_expr_covers_explicit_nodes() -> None:
    """Explicit tuple, text, ellipsis, union-base, and runtime nodes render deterministically."""
    union = PythonTypeUnion((PythonTypeName("str"), PythonTypeName("None")))
    expression = PythonTypeSubscript(
        union,
        (
            PythonTypeTuple((PythonTypeLiteralValue("x"),)),
            PythonTypeTuple((PythonTypeLiteralValue(1), PythonTypeLiteralValue(2))),
            PythonTypeOpaqueText("ForwardRefName"),
            PythonTypeEllipsis(),
        ),
    )

    assert render_python_type_expr(expression) == "(str | None)[('x',), (1, 2), ForwardRefName, ...]"
    assert render_python_type_expr(PythonTypeRuntimeSymbol("pkg.models", ("Outer", "Inner"))) == (
        "pkg.models.Outer.Inner"
    )


def test_render_python_type_expr_round_trips_empty_tuple_variadics_and_infinite_floats() -> None:
    """Canonical nodes round-trip identically on the Python 3.10/3.12/3.14 test matrix.

    In particular, the runtime tokenizer may only bridge the shared outer
    variadic marker; it must not make the IR depend on runtime-specific grammar.
    """
    annotations = [
        "tuple[()]",
        "tuple[*Ts]",
        "Callable[[int, *Ts], bool]",
        "Literal[1e309, -1e309]",
    ]

    for annotation in annotations:
        expression = parse_python_type_annotation(annotation)
        assert expression is not None
        rendered = render_python_type_expr(expression)
        assert parse_python_type_annotation(rendered) == expression

    assert render_python_type_expr(PythonTypeLiteralValue(inf)) == "1e309"
    assert render_python_type_expr(PythonTypeLiteralValue(-inf)) == "-1e309"
    assert render_python_type_expr(PythonTypeSubscript(PythonTypeName("tuple"), ())) == "tuple[()]"
    assert render_python_type_expr(PythonTypeStarred(PythonTypeUnion((PythonTypeName("A"), PythonTypeName("B"))))) == (
        "*(A | B)"
    )


def test_render_python_type_expr_rejects_unknown_node() -> None:
    """The renderer fails explicitly when a future node lacks handling."""

    class UnknownPythonTypeExpr(PythonTypeExpr):
        pass

    with pytest.raises(TypeError, match="UnknownPythonTypeExpr"):
        render_python_type_expr(UnknownPythonTypeExpr())


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        (PythonTypeName("str"), "str"),
        (PythonTypeQualifiedName(("foo", "Bar")), "Bar"),
        (PythonTypeRuntimeSymbol("foo", ("Outer", "Inner")), "Inner"),
        (
            PythonTypeSubscript(PythonTypeRuntimeSymbol("foo", ("Outer", "Inner")), (PythonTypeName("str"),)),
            "Inner",
        ),
        (PythonTypeOpaqueText("Forward"), ""),
    ],
)
def test_python_type_expr_base_name(expression: PythonTypeExpr, expected: str) -> None:
    """Base names are derived structurally for each name-bearing node."""
    assert python_type_expr_base_name(expression) == expected


def test_python_type_expr_arguments_returns_structured_children() -> None:
    """Only subscript and union nodes expose top-level arguments."""
    arguments = (PythonTypeName("str"), PythonTypeName("int"))

    assert python_type_expr_arguments(PythonTypeSubscript(PythonTypeName("dict"), arguments)) is arguments
    assert python_type_expr_arguments(PythonTypeUnion(arguments)) is arguments
    assert python_type_expr_arguments(PythonTypeName("str")) == ()


def test_iter_python_type_expr_qualified_names_uses_stable_structure_order() -> None:
    """Qualified syntactic and runtime names follow a stable depth-first order."""
    expression = PythonTypeSubscript(
        PythonTypeQualifiedName(("typing", "Callable")),
        (
            PythonTypeParameterList((
                PythonTypeStarred(PythonTypeQualifiedName(("variadic", "Ts"))),
                PythonTypeQualifiedName(("foo", "Bar")),
                PythonTypeTuple((PythonTypeRuntimeSymbol("pkg.models", ("Outer", "Inner")),)),
            )),
            PythonTypeUnion((
                PythonTypeQualifiedName(("baz", "Qux")),
                PythonTypeOpaqueText("Forward"),
            )),
        ),
    )

    assert list(iter_python_type_expr_qualified_names(expression)) == [
        "typing.Callable",
        "variadic.Ts",
        "foo.Bar",
        "pkg.models.Outer.Inner",
        "baz.Qux",
    ]
    assert list(iter_python_type_expr_names(expression)) == ["Callable", "Ts", "Bar", "Inner", "Qux"]


def test_rewrite_python_type_expr_shares_unchanged_tree() -> None:
    """An identity rewrite returns the original root and all of its subtrees."""
    expression = parse_python_type_annotation("Callable[[foo.Bar, tuple[int, str]], baz.Qux | None]")
    assert expression is not None

    rewritten = rewrite_python_type_expr(expression, lambda leaf: leaf)

    assert rewritten is expression


def test_rewrite_python_type_expr_rebuilds_only_changed_paths() -> None:
    """A leaf change rebuilds its ancestors while sharing unrelated branches."""
    expression = parse_python_type_annotation("Callable[[foo.Bar, tuple[int, str]], baz.Qux | None]")
    assert isinstance(expression, PythonTypeSubscript)
    original_return = expression.arguments[1]

    def replace_foo_bar(leaf: PythonTypeExpr) -> PythonTypeExpr:
        if leaf == PythonTypeQualifiedName(("foo", "Bar")):
            return PythonTypeName("LocalBar")
        return leaf

    rewritten = rewrite_python_type_expr(expression, replace_foo_bar)

    assert isinstance(rewritten, PythonTypeSubscript)
    assert rewritten is not expression
    assert rewritten.base is expression.base
    assert rewritten.arguments[1] is original_return
    assert render_python_type_expr(rewritten) == "Callable[[LocalBar, tuple[int, str]], baz.Qux | None]"


def test_rewrite_python_type_expr_handles_tuple_union_and_leaf_roots() -> None:
    """Tuple, union, and direct leaf roots participate in rewriting."""
    expression = PythonTypeTuple((
        PythonTypeUnion((PythonTypeName("Old"), PythonTypeName("str"))),
        PythonTypeLiteralValue(1),
    ))

    def replace_old(leaf: PythonTypeExpr) -> PythonTypeExpr:
        return PythonTypeName("New") if leaf == PythonTypeName("Old") else leaf

    rewritten = rewrite_python_type_expr(expression, replace_old)

    assert render_python_type_expr(rewritten) == "(New | str, 1)"
    assert rewrite_python_type_expr(expression, lambda leaf: leaf) is expression
    assert rewrite_python_type_expr(PythonTypeName("Old"), replace_old) == PythonTypeName("New")


def test_rewrite_python_type_expr_handles_starred_values_with_structural_sharing() -> None:
    """Variadic wrappers share identity unless their contained expression changes."""
    expression = PythonTypeStarred(PythonTypeName("Old"))

    assert rewrite_python_type_expr(expression, lambda leaf: leaf) is expression
    assert rewrite_python_type_expr(expression, lambda _leaf: PythonTypeName("New")) == PythonTypeStarred(
        PythonTypeName("New")
    )
