"""Static scan of regex syntax: which patterns every engine runs alike, and the conjunction of patterns.

The scan follows the Rust grammar and never reads the installed pydantic-core or the running Python, so the
decisions made from it do not depend on the environment that generates. A leaf: the parser's keyword table
(``parser/_intersection.py``), the literal check (``_constraint_applicability.py``) and the backends read it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import cache
from typing import TYPE_CHECKING, Final

from typing_extensions import final

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = [
    "PatternConjunction",
    "conjoin_patterns",
    "has_lookaround",
    "is_portable_pattern",
    "literal_pattern_value",
    "lookahead_patterns",
    "pairwise_patterns",
    "pattern_admits",
    "pattern_implies",
    "scan_regex_syntax",
]

_LOOKAROUND_PATTERN: Final = r"\(\?<?[=!]"


_UNICODE_WHITE_SPACE = "[\t-\r \x85\xa0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]*"
_REGEX_ESCAPE_TOKENS = r"\\[pPxuUbB]\{[^}]*\}?|\\(?P<escape>.)?|(?P<open>\[\^?\]?)"
_REGEX_TOKEN: Final = (
    rf"(?s){_REGEX_ESCAPE_TOKENS}|\(\?P?<(?![=!])[^>()\\]*>|\(\?(?P<group>[>(#]|P=)|\(\?(?P<flags>[A-Za-z-]*)[:)]"
    rf"|(?:\{{{_UNICODE_WHITE_SPACE}[0-9]+{_UNICODE_WHITE_SPACE}"
    rf"(?:,{_UNICODE_WHITE_SPACE}(?:[0-9]+{_UNICODE_WHITE_SPACE})?)?\}}|[*+?])(?P<possessive>\+)?|(?P<brace>\{{)"
)
_REGEX_CLASS_TOKEN: Final = rf"(?s)\[:\^?[A-Za-z]+:\]|{_REGEX_ESCAPE_TOKENS}|(?P<close>\])"
_RUST_REJECTED_ESCAPES = frozenset("0123456789NZ")
_RUST_REJECTED_CLASS_ESCAPES = frozenset("0123456789NZb")
_RUST_REJECTED_FLAGS = frozenset("aL")
_PY_310 = (3, 10)
_PY_311 = (3, 11)
_PY_314 = (3, 14)


@cache
def _compiled(source: str) -> re.Pattern[str]:
    """Compile one of this module's own patterns on first use, so that importing it costs no compilation."""
    return re.compile(source)


def has_lookaround(pattern: str) -> bool:
    """Return whether a pattern holds a lookahead or lookbehind."""
    return _compiled(_LOOKAROUND_PATTERN).search(pattern) is not None


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
    while (match := _compiled(_REGEX_CLASS_TOKEN if depth else _REGEX_TOKEN).search(pattern, position)) is not None:
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
    return not has_lookaround(pattern) and (scan := scan_regex_syntax(pattern)) is not None and not scan[0]


_REGEX_META_CHARACTERS: Final = frozenset(r"\.^$*+?{}[]|()")
_LITERAL_PATTERN: Final = r"(?:[^\\.^$*+?{}\[\]|()]|\\[\\.^$*+?{}\[\]|()])*"
_HEAD_CHARACTER: Final = r"[^\\.^$*+?{}\[\]|()]|\\[\\.^$*+?{}\[\]|()]"
_QUANTIFIER_STARTS: Final = frozenset("*+?{")
_SINGLE_CLASS: Final = r"(?s)\\[dDwWsSntrfv]|\\x[0-9A-Fa-f]{2}|\[\^?\]?(?:[^\\\[\]]|\\.)*\]"
_COUNTED_REPETITION: Final = (
    rf"\{{{_UNICODE_WHITE_SPACE}[0-9]+{_UNICODE_WHITE_SPACE}"
    rf"(?:,{_UNICODE_WHITE_SPACE}(?:[0-9]+{_UNICODE_WHITE_SPACE})?)?\}}"
)
_PLAIN_REPETITION: Final = r"\{[0-9]+(?:,[0-9]*)?\}"
_NON_ASCII_CLASS_ESCAPE: Final = r"(?<!\\)(?:\\\\)*\\[sSwWdDbB]"
_GROUP_NAME: Final = r"\(\?(?P<python>P?)<(?![=!])(?P<name>[^>()\\]*)>"
_FLAG_GROUP: Final = r"\(\?(?P<flags>[A-Za-z-]*)(?P<end>[:)])"
_SHARED_FLAGS: Final = frozenset("imsxu-")
_BRACED_ESCAPE_LETTERS: Final = frozenset("pPxuUbB")
_PYTHON_ESCAPE_DIFFERENCES: Final = frozenset("pP<>z")
_CLASS_SET_OPERATIONS: Final = ("&&", "--", "~~")
_PLAIN_ASCII_CONTROLS: Final = frozenset("\t\n\x0b\x0c\r")


def literal_pattern_value(pattern: str) -> str | None:
    """Return the string a pattern of plain characters and escaped regex metacharacters only searches for."""
    if _REGEX_META_CHARACTERS.isdisjoint(pattern):
        return pattern
    if _compiled(_LITERAL_PATTERN).fullmatch(pattern) is None:
        return None
    return re.sub(r"\\(.)", r"\1", pattern)


@final
@dataclass(frozen=True, slots=True)
class _Structure:
    """What a scan of a pattern's groups, classes and escapes (the Rust grammar) finds."""

    top_level_alternation: bool
    global_flags: tuple[int, ...]
    group_names: frozenset[str]
    read_alike: bool


def _escape_end(pattern: str, position: int) -> tuple[int, bool]:
    """Return where the escape at ``position`` ends, and whether Python's ``re`` reads it as Rust does."""
    letter = pattern[position + 1 : position + 2]
    if letter in _BRACED_ESCAPE_LETTERS and pattern.startswith("{", position + 2):
        return (pattern.find("}", position) + 1 or len(pattern)), False
    return position + 2, letter not in _PYTHON_ESCAPE_DIFFERENCES


def _class_end(pattern: str, position: int) -> tuple[int, bool]:
    """Return where the character class opened at ``position`` ends, and whether Python reads it as Rust does.

    Python reads nested and POSIX classes and the set operations ``&&``, ``--`` and ``~~`` as plain characters.
    """
    position += 1 + pattern.startswith("^", position + 1)
    position += pattern.startswith("]", position)
    depth = 1
    alike = True
    while position < len(pattern) and depth:
        if pattern[position] == "\\":
            position, escape_alike = _escape_end(pattern, position)
            alike &= escape_alike
            continue
        if pattern.startswith(_CLASS_SET_OPERATIONS, position):
            alike = False
            position += 2
            continue
        if pattern[position] == "[":
            alike = False
            depth += 1
        elif pattern[position] == "]":
            depth -= 1
        position += 1
    return position, alike


def _structure(pattern: str) -> _Structure:
    """Scan a pattern's top level, group names, global flag groups and the syntax Python reads differently.

    Python reads ``(?<name>...)`` and the flags ``U`` and ``R`` not at all, group names that are not identifiers
    not as names, and a counted repetition with whitespace (``a{ 2 }``) as literal text where Rust repeats.
    """
    alternation = False
    flags: list[int] = []
    names: set[str] = set()
    alike = True
    depth = 0
    position = 0
    while position < len(pattern):
        character = pattern[position]
        if character == "\\":
            position, escape_alike = _escape_end(pattern, position)
            alike &= escape_alike
            continue
        if character == "[":
            position, class_alike = _class_end(pattern, position)
            alike &= class_alike
            continue
        if character == "(":
            depth += 1
            if (name := _compiled(_GROUP_NAME).match(pattern, position)) is not None:
                names.add(name["name"])
                alike &= bool(name["python"]) and name["name"].isidentifier()
            elif (group := _compiled(_FLAG_GROUP).match(pattern, position)) is not None:
                alike &= _SHARED_FLAGS.issuperset(group["flags"])
                if group["end"] == ")":
                    flags.append(position)
        elif character == "{":
            alike &= (repetition := _compiled(_COUNTED_REPETITION).match(pattern, position)) is None or (
                _compiled(_PLAIN_REPETITION).fullmatch(repetition.group()) is not None
            )
        elif character == ")":
            depth -= 1
        elif character == "|" and not depth:
            alternation = True
        position += 1
    return _Structure(alternation, tuple(flags), frozenset(names), alike)


def _head(pattern: str) -> tuple[bool, str] | None:
    """Return whether every match of a pattern starts the string, and the literal every match starts with.

    None when the pattern has a top-level alternative, whose matches need not start alike.
    """
    if _structure(pattern).top_level_alternation:
        return None
    anchored = pattern.startswith(("^", "\\A"))
    position = 2 if pattern.startswith("\\A") else int(anchored)
    literal: list[str] = []
    while (character := _compiled(_HEAD_CHARACTER).match(pattern, position)) is not None and (
        pattern[character.end() : character.end() + 1] not in _QUANTIFIER_STARTS
    ):
        literal.append(character.group()[-1])
        position = character.end()
    return anchored, "".join(literal)


def _search(pattern: str, value: str, *, full: bool = False) -> bool | None:
    """Return whether Python's ``re`` finds a pattern in a string, or matches all of it; None if it cannot compile."""
    try:
        compiled = re.compile(pattern)
    except re.error:
        return None
    return (compiled.fullmatch(value) if full else compiled.search(value)) is not None


def _class_admits(pattern: str, character: str) -> bool:
    """Return whether a pattern that is one character class or class escape admits a printable ASCII character."""
    return (
        _compiled(_SINGLE_CLASS).fullmatch(pattern) is not None
        and character.isascii()
        and (character.isprintable() or character in _PLAIN_ASCII_CONTROLS)
        and _structure(pattern).read_alike
        and bool(_search(pattern, character, full=True))
    )


def pattern_implies(pattern: str, other: str) -> bool:
    r"""Return whether ``other`` matches wherever ``pattern`` matches, at the same position.

    It does when both are equal, or when every match of ``pattern`` starts with a literal that ``other`` (a literal
    prefix of it, or one character class or class escape admitting its first character) matches. ``other`` may
    start with ``^`` or ``\A`` only when ``pattern`` does.
    """
    if pattern == other:
        return True
    if (head := _head(pattern)) is None:
        return False
    anchored, literal = head
    body = other.removeprefix("\\A") if other.startswith("\\A") else other.removeprefix("^")
    if body != other and not anchored:
        return False
    if (other_literal := literal_pattern_value(body)) is not None:
        return literal.startswith(other_literal)
    return bool(literal) and _class_admits(body, literal[0])


def _reads_alike(pattern: str) -> bool:
    """Return whether every pydantic engine and Python from 3.10 on read a pattern alike.

    Rust and Python's ``re`` parse it (``is_portable_pattern``, no syntax that needs a later Python), Python reads
    every escape, group and class of it as Rust does, and a global flag group starts it if it has one.
    """
    if not is_portable_pattern(pattern) or (scan := scan_regex_syntax(pattern)) is None or scan[1] != _PY_310:
        return False
    structure = _structure(pattern)
    return structure.read_alike and all(position == 0 for position in structure.global_flags)


def pattern_admits(pattern: str, value: str) -> bool | None:
    r"""Return whether a string meets a pattern, or None when no static rule certifies that every engine agrees.

    The pattern must read alike everywhere (``_reads_alike``), and a ``$`` is not decided on a string that ends with
    a newline (Python's ``$`` matches before it). A pattern Python cannot compile is never decided, nor is a string
    with non-ASCII characters against a pattern that uses ``\s \S \w \W \d \D \b \B``, which the engines define
    differently beyond ASCII.
    """
    if (
        not _reads_alike(pattern)
        or ("$" in pattern and value.endswith("\n"))
        or (not value.isascii() and _compiled(_NON_ASCII_CLASS_ESCAPE).search(pattern) is not None)
    ):
        return None
    return _search(pattern, value)


@final
@dataclass(frozen=True, slots=True)
class PatternConjunction:
    """The pattern that a value meets exactly when it meets every pattern of the parts, and those it leaves out.

    ``untaken`` lists the patterns no single expression conjoins with the first (``conjoin_patterns``).
    """

    pattern: str
    untaken: tuple[str, ...] = ()


def pairwise_patterns(patterns: Sequence[str]) -> str:
    """Return the lookaheads ``(?=p1)(?=p2)`` of the patterns in order, nested pairwise; equal neighbours as one."""
    match patterns:
        case [pattern]:
            return pattern
        case [left, right]:
            return left if left == right else f"(?={left})(?={right})"
    result = patterns[0]
    for pattern in patterns[1:]:
        result = pairwise_patterns((result, pattern))
    return result


def lookahead_patterns(patterns: Sequence[str]) -> str:
    r"""Return the lookaheads of ``patterns`` as 0.86.0 wrote them, the output without --intersect-allof-keywords.

    Equal neighbours are one and pairs become ``(?=p1)(?=p2)``, nested pairwise; literal patterns none of which is a
    prefix of the longest get ``\A(?=[\s\S]*a)(?=[\s\S]*b)``. No check that the patterns read alike on every engine
    or conjoin into one expression (``conjoin_patterns`` does).
    """
    match patterns:
        case [pattern]:
            return pattern
        case [left, right] if left == right:
            return left
    literals = [literal_pattern_value(pattern) for pattern in patterns]
    if all(literal is not None for literal in literals):
        longest = max((literal for literal in literals if literal is not None), key=str.__len__)
        if any(literal is not None and not longest.startswith(literal) for literal in literals):
            return r"\A" + "".join(rf"(?=[\s\S]*{pattern})" for pattern in dict.fromkeys(patterns))
    match patterns:
        case [left, right]:
            return f"(?={left})(?={right})"
    result = patterns[0]
    for pattern in patterns[1:]:
        result = lookahead_patterns((result, pattern))
    return result


def _conjoinable(patterns: Sequence[str]) -> bool:
    """Return whether one expression conjoins patterns: each reads alike, sets no global flags and repeats no name."""
    names: set[str] = set()
    for pattern in patterns:
        structure = _structure(pattern)
        if not structure.read_alike or structure.global_flags or not names.isdisjoint(structure.group_names):
            return False
        names |= structure.group_names
    return True


def _meet_at_one_position(patterns: Sequence[str]) -> bool:
    """Return whether the lookaheads of distinct patterns meet wherever each pattern matches.

    They do when every pattern starts the string, or when one pattern implies the others (``pattern_implies``).
    """
    heads = [_head(pattern) for pattern in patterns]
    return all(head is not None and head[0] for head in heads) or any(
        all(pattern_implies(pattern, other) for other in patterns) for pattern in patterns
    )


def conjoin_patterns(patterns: Sequence[str]) -> PatternConjunction:
    r"""Return the pattern a string meets exactly when it meets each of ``patterns``, searched as JSON Schema does.

    Equal patterns are one, and the lookaheads ``(?=p1)(?=p2)`` stay where they meet at one position
    (``_meet_at_one_position``). Other patterns get ``\A(?=[\s\S]*?(?:p1))(?=[\s\S]*?(?:p2))``, literal patterns
    ``\A(?=[\s\S]*a)(?=[\s\S]*b)``. Patterns no single expression conjoins (``_conjoinable``), or that do not run
    on every engine (``is_portable_pattern``), keep the first, listing the others as untaken.
    """
    distinct = list(dict.fromkeys(patterns))
    literal = all(literal_pattern_value(pattern) is not None for pattern in distinct)
    if not (literal or (all(map(is_portable_pattern, distinct)) and _conjoinable(distinct))):
        return PatternConjunction(patterns[0], tuple(pattern for pattern in distinct if pattern != patterns[0]))
    if _meet_at_one_position(distinct):
        return PatternConjunction(pairwise_patterns(patterns))
    search = r"(?=[\s\S]*{})" if literal else r"(?=[\s\S]*?(?:{}))"
    return PatternConjunction(r"\A" + "".join(search.format(pattern) for pattern in distinct))
