"""Tests for target-independent rendered decorator matching."""

from __future__ import annotations

import subprocess
import sys
import tokenize

import pytest

from datamodel_code_generator._python_decorator import is_named_python_decorator


@pytest.mark.allow_direct_assert
@pytest.mark.parametrize(
    ("decorator", "name", "expected"),
    [
        pytest.param("@deprecated", "deprecated", True, id="name"),
        pytest.param("@deprecated('message')", "deprecated", True, id="call"),
        pytest.param("@deprecated (message='reason')", "deprecated", True, id="keyword-call"),
        pytest.param("@deprecated(f'{reason=}')", "deprecated", True, id="f-string-debug-expression"),
        pytest.param("@deprecated(t'{reason}')", "deprecated", True, id="future-template-string"),
        pytest.param(
            "@deprecated(t'{reason=}')",
            "deprecated",
            True,
            id="future-template-string-debug-expression",
        ),
        pytest.param(
            """@deprecated(f"{f'{reason=}'}")""",
            "deprecated",
            True,
            id="nested-f-string-debug-expression",
        ),
        pytest.param(
            "@deprecated(\n    # Kept opaque for a newer target grammar.\n    t'{reason}',\n)",
            "deprecated",
            True,
            id="multiline-future-template-string",
        ),
        pytest.param("@deprecated(lambda value: (value,))", "deprecated", True, id="nested-group"),
        pytest.param("@(deprecated)", "deprecated", True, id="parenthesized-name"),
        pytest.param("@(deprecated)('message')", "deprecated", True, id="parenthesized-call"),
        pytest.param("@((deprecated))('message')", "deprecated", True, id="nested-parenthesized-call"),
        pytest.param("@((deprecated('message')))", "deprecated", True, id="parenthesized-direct-call"),
        pytest.param("@ (deprecated)", "deprecated", True, id="spaced-parenthesized-name"),
        pytest.param("@( ( deprecated ) )", "deprecated", True, id="internally-spaced-parenthesized-name"),
        pytest.param("@ deprecated", "deprecated", True, id="spaced-name"),
        pytest.param("@deprecated # comment", "deprecated", True, id="trailing-comment"),
        pytest.param("@deprecated_custom", "deprecated", False, id="prefixed-name"),
        pytest.param("@deprecated_custom()", "deprecated", False, id="prefixed-call"),
        pytest.param("@deprecated1", "deprecated", False, id="numeric-suffix-name"),
        pytest.param("@deprecated1()", "deprecated", False, id="numeric-suffix-call"),
        pytest.param("@(deprecated_custom)", "deprecated", False, id="parenthesized-prefixed-name"),
        pytest.param("@(deprecated.factory)", "deprecated", False, id="parenthesized-attribute"),
        pytest.param("@module.deprecated", "deprecated", False, id="qualified-name"),
        pytest.param("@deprecated.factory()", "deprecated", False, id="attribute-call"),
        pytest.param("@deprecated[str]", "deprecated", False, id="subscript"),
        pytest.param("@deprecated()()", "deprecated", False, id="nested-call"),
        pytest.param("@factory(deprecated)", "deprecated", False, id="nested-argument"),
        pytest.param("deprecated", "deprecated", False, id="missing-marker"),
        pytest.param("@deprecated(", "deprecated", False, id="unclosed-call"),
        pytest.param('@deprecated(f"{', "deprecated", False, id="unterminated-f-string"),
        pytest.param("@deprecated(]", "deprecated", False, id="mismatched-call"),
        pytest.param("@deprecated # comment\n()", "deprecated", False, id="call-after-comment"),
        pytest.param("@deprecated\n()", "deprecated", False, id="call-after-logical-line"),
        pytest.param("@deprecated(, 'message')", "deprecated", False, id="leading-argument-comma"),
        pytest.param("@deprecated('message',, reason=True)", "deprecated", False, id="repeated-argument-comma"),
        pytest.param("@deprecated(=reason)", "deprecated", False, id="leading-keyword-equals"),
        pytest.param("@deprecated(reason=)", "deprecated", False, id="missing-keyword-value"),
        pytest.param("@deprecated('message'; reason=True)", "deprecated", False, id="argument-semicolon"),
        pytest.param(
            "@deprecated(t'{reason=}',, reason=True)",
            "deprecated",
            False,
            id="repeated-comma-after-future-string",
        ),
        pytest.param(
            "@deprecated(f'{reason=}'; reason=True)",
            "deprecated",
            False,
            id="semicolon-after-f-string",
        ),
        pytest.param(
            "@deprecated(reason=f'{reason=}', missing=)",
            "deprecated",
            False,
            id="missing-keyword-value-after-f-string",
        ),
        pytest.param("@deprecated((,))", "deprecated", False, id="nested-leading-comma"),
        pytest.param("@deprecated(\ud800)", "deprecated", False, id="surrogate-tokenizer-failure"),
        pytest.param("@(1)", "deprecated", False, id="parenthesized-non-name"),
        pytest.param("@1", "1", False, id="invalid-requested-name"),
        pytest.param("@class", "class", False, id="keyword"),
        pytest.param("@# no decorator target", "deprecated", False, id="comment-only"),
        pytest.param("@", "deprecated", False, id="empty"),
    ],
)
def test_is_named_python_decorator(decorator: str, name: str, *, expected: bool) -> None:
    """Match only exact direct targets across host and target syntax versions."""
    assert is_named_python_decorator(decorator, name) is expected


@pytest.mark.allow_direct_assert
def test_is_named_python_decorator_handles_deep_parenthesized_target() -> None:
    """Avoid parser and tokenizer recursion limits for a deeply wrapped target."""
    wrapper_count = 2_000
    decorator = f"@{'(' * wrapper_count}deprecated{')' * wrapper_count}"

    assert is_named_python_decorator(decorator, "deprecated") is True


@pytest.mark.allow_direct_assert
def test_is_named_python_decorator_handles_runtime_tokenizer_syntax_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject runtime-specific tokenizer SyntaxError at the external boundary."""

    def raise_syntax_error(_readline: object) -> None:
        raise SyntaxError

    is_named_python_decorator.cache_clear()
    monkeypatch.setattr(tokenize, "generate_tokens", raise_syntax_error)

    assert is_named_python_decorator("@deprecated(syntax_error_probe", "deprecated") is False


@pytest.mark.allow_direct_assert
def test_model_base_import_keeps_python_decorator_helper_lazy() -> None:
    """Import the decorator matcher only when deprecated metadata is rendered."""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; "
                "import datamodel_code_generator.model.base; "
                "print('datamodel_code_generator._python_decorator' in sys.modules)"
            ),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert result.stdout == "False\n"
