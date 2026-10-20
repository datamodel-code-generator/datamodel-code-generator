"""Static scan of regex syntax: which patterns the default engine of every supported pydantic runs.

The scan follows the Rust grammar and never reads the installed pydantic-core or the running Python, so the
decisions made from it do not depend on the environment that generates.
"""

from __future__ import annotations

import re

__all__ = ["LOOKAROUND_PATTERN", "is_portable_pattern", "scan_regex_syntax"]

LOOKAROUND_PATTERN: re.Pattern[str] = re.compile(r"\(\?<?[=!]")


_UNICODE_WHITE_SPACE = "[\t-\r \x85\xa0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]*"
_REGEX_ESCAPE_TOKENS = r"\\[pPxuUbB]\{[^}]*\}?|\\(?P<escape>.)?|(?P<open>\[\^?\]?)"
_REGEX_TOKEN: re.Pattern[str] = re.compile(
    rf"{_REGEX_ESCAPE_TOKENS}|\(\?P?<(?![=!])[^>()\\]*>|\(\?(?P<group>[>(#]|P=)|\(\?(?P<flags>[A-Za-z-]*)[:)]"
    rf"|(?:\{{{_UNICODE_WHITE_SPACE}[0-9]+{_UNICODE_WHITE_SPACE}"
    rf"(?:,{_UNICODE_WHITE_SPACE}(?:[0-9]+{_UNICODE_WHITE_SPACE})?)?\}}|[*+?])(?P<possessive>\+)?|(?P<brace>\{{)",
    re.DOTALL,
)
_REGEX_CLASS_TOKEN: re.Pattern[str] = re.compile(rf"\[:\^?[A-Za-z]+:\]|{_REGEX_ESCAPE_TOKENS}|(?P<close>\])", re.DOTALL)
_RUST_REJECTED_ESCAPES = frozenset("0123456789NZ")
_RUST_REJECTED_CLASS_ESCAPES = frozenset("0123456789NZb")
_RUST_REJECTED_FLAGS = frozenset("aL")
_PY_310 = (3, 10)
_PY_311 = (3, 11)
_PY_314 = (3, 14)


def _regex_token_requirements(match: re.Match[str]) -> tuple[bool, tuple[int, int]] | None:
    """Return whether a token outside character classes is Rust-rejected and the Python its syntax needs."""
    if (escape := match["escape"]) is not None:
        return escape in _RUST_REJECTED_ESCAPES, _PY_314 if escape == "z" else _PY_310
    if (flags := match["flags"]) is not None:
        return None if "x" in flags else (not _RUST_REJECTED_FLAGS.isdisjoint(flags), _PY_310)
    if group := match["group"]:
        return True, _PY_311 if group == ">" else _PY_310
    return match["brace"] is not None, _PY_311 if match["possessive"] else _PY_310


def scan_regex_syntax(pattern: str) -> tuple[bool, tuple[int, int]] | None:
    r"""Return whether the Rust regex crate rejects a pattern's syntax, and the oldest Python that parses it.

    The scan follows the Rust grammar of every pydantic-core release from 2.3.0 on, so it never reports a
    pattern Rust accepts. Capture names such as ``(?P<ids[]>...)``, nested and POSIX character classes, braced
    escapes such as ``\p{L}``, ``\x{41}`` or ``\b{start}``, and counted repetitions with whitespace are
    consumed whole, with separate tokens inside and outside character classes. It reports backreferences and
    octal escapes, ``\N{...}``, ``\Z``, ``[\b]``, ``(?P=name)``, atomic groups, conditionals, comments, the
    ``a`` and ``L`` flags, and braces that cannot start a counted repetition. Atomic groups and possessive
    quantifiers need Python 3.11, and ``\z`` needs Python 3.14. Returns None for verbose patterns, whose
    whitespace and comments the scan does not model.
    """
    rejected = False
    minimum_python = _PY_310
    depth = 0
    position = 0
    while (match := (_REGEX_CLASS_TOKEN if depth else _REGEX_TOKEN).search(pattern, position)) is not None:
        position = match.end()
        if depth or match["open"]:
            depth += bool(match["open"]) - bool(depth and match["close"])
            rejected |= match["escape"] in _RUST_REJECTED_CLASS_ESCAPES
            continue
        if (requirements := _regex_token_requirements(match)) is None:
            return None
        rejected |= requirements[0]
        minimum_python = max(minimum_python, requirements[1])
    return (rejected, minimum_python)


def is_portable_pattern(pattern: str) -> bool:
    """Return whether a pattern runs on the default regex engine of every supported pydantic."""
    return (
        LOOKAROUND_PATTERN.search(pattern) is None and (scan := scan_regex_syntax(pattern)) is not None and not scan[0]
    )
