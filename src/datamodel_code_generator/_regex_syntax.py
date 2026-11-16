"""Static scan of regex syntax: which patterns every engine runs alike, which need Python's ``re``, and conjunctions.

The scan follows the Rust grammar and never reads the installed pydantic-core or the running Python, so the
decisions made from it do not depend on the environment that generates. A leaf: the parser's keyword table
(``parser/_intersection.py``), the literal check (``_constraint_applicability.py``) and the backends read it.
"""

from __future__ import annotations

import math
import re
from functools import cache
from types import MappingProxyType
from typing import TYPE_CHECKING, Final, Literal, TypeAlias, cast

from typing_extensions import final

from datamodel_code_generator._python_syntax import holds_lone_surrogate
from datamodel_code_generator._records import record

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Sequence

__all__ = [
    "EngineRow",
    "PatternConjunction",
    "PatternEngine",
    "conjoin_patterns",
    "control_escapes_keep_meaning",
    "has_lookaround",
    "is_portable_pattern",
    "literal_pattern_value",
    "lookahead_patterns",
    "pairwise_patterns",
    "pattern_admits",
    "pattern_engine",
    "pattern_implies",
    "python_rejects",
    "read_pattern",
    "scan_regex_syntax",
    "selects_python_engine",
]

EngineRow: TypeAlias = Literal["model", "adapter"]
"""What selects the ``python-re`` engine of a pattern: the config of a model, or a schema validator's adapter."""
PatternEngine: TypeAlias = Literal["python", "surrogate", "default"]
"""What a pattern asks of the config that builds it (``pattern_engine``): ``python-re`` for its syntax, ``python-re``
for a lone surrogate where the config's other patterns allow it (``selects_python_engine``), or the default engine."""

_LOOKAROUND_PATTERN: Final = r"\(\?<?[=!]"
_WHITE_SPACE_CHARACTERS = "\t-\r \x85\xa0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000"
_UNICODE_WHITE_SPACE = f"[{_WHITE_SPACE_CHARACTERS}]*"
_VERBOSE_SPACE = rf"(?:[{_WHITE_SPACE_CHARACTERS}]|#[^\n]*(?=\n|\Z))*"
_CODE_ESCAPE: Final = r"\\u[0-9A-Fa-f]{4}|\\U[0-9A-Fa-f]{8}|\\[xuU]\{[0-9A-Fa-f]+\}"
_REGEX_ESCAPE_TOKENS = (
    rf"(?P<code>{_CODE_ESCAPE})"
    r"|\\[pPxuUbB]\{[^}]*\}?|\\(?P<escape>.)?|(?P<open>\[\^?\]?)"
)


def _counted_repetition(space: str) -> str:
    """Return the pattern of a counted repetition whose parts may be separated by ``space``, with its two counts."""
    return rf"\{{{space}(?P<lower>[0-9]+){space}(?:,{space}(?:(?P<upper>[0-9]+){space})?)?\}}"


def _regex_token(counted_repetition: str) -> str:
    """Return the token pattern of the scan, which reads a counted repetition as ``counted_repetition`` does."""
    return (
        rf"(?s){_REGEX_ESCAPE_TOKENS}|\(\?P?<(?![=!])[^>()\\]*>|\(\?(?P<group>[>(#]|P=)|\(\?(?P<flags>[A-Za-z-]*)[:)]"
        rf"|(?:{counted_repetition}|[*+?])(?P<possessive>\+)?|(?P<brace>\{{)"
    )


_COUNTED_REPETITION: Final = _counted_repetition(_UNICODE_WHITE_SPACE)
_REGEX_TOKEN: Final = _regex_token(_COUNTED_REPETITION)
_REGEX_CLASS_TOKEN: Final = rf"(?s)\[:\^?[A-Za-z]+:\]|{_REGEX_ESCAPE_TOKENS}|(?P<close>\])"
_REGEX_VERBOSE_TOKEN: Final = rf"{_regex_token(_counted_repetition(_VERBOSE_SPACE))}|(?P<comment>#[^\n]*)"
_REGEX_VERBOSE_CLASS_TOKEN: Final = rf"{_REGEX_CLASS_TOKEN}|(?P<comment>#[^\n]*)"
_VERBOSE_FLAGS: Final = r"\(\?(?P<flags>[A-Za-z]*x[A-Za-z]*(?:-[A-Za-wyz]+)?)\)"
_RUST_REJECTED_ESCAPES = frozenset("0123456789NZ")
_RUST_REJECTED_CLASS_ESCAPES = frozenset("0123456789NZb")
_RUST_REJECTED_FLAGS = frozenset("aL")
_FIRST_SURROGATE = 0xD800
_LAST_SURROGATE = 0xDFFF
_LAST_CODE_POINT = 0x10FFFF
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


def _exceeds(lower: str | None, upper: str | None) -> bool:
    """Return whether the digits ``lower`` stand for more than the digits ``upper``, compared as text, not parsed."""
    if lower is None or upper is None:
        return False
    lower, upper = lower.lstrip("0"), upper.lstrip("0")
    return (len(lower), lower) > (len(upper), upper)


def _names_no_scalar_value(code: str | None) -> bool:
    r"""Return whether a hexadecimal escape names a surrogate or a code point past U+10FFFF, which Rust rejects.

    Python's ``re`` reads ``\uD800`` and ``\U0000D800`` as the surrogate they name; no braced escape (``\x{D800}``).
    """
    if code is None:
        return False
    value = int(code[2:].strip("{}"), 16)
    return _FIRST_SURROGATE <= value <= _LAST_SURROGATE or value > _LAST_CODE_POINT


def _regex_token_requirements(match: re.Match[str]) -> tuple[bool, tuple[int, int]] | None:
    """Return whether a token outside character classes is Rust-rejected and the Python its syntax needs."""
    if (code := match["code"]) is not None:
        return _names_no_scalar_value(code), _PY_310
    if (escape := match["escape"]) is not None:
        return escape in _RUST_REJECTED_ESCAPES, _PY_314 if escape == "z" else _PY_310
    if (flags := match["flags"]) is not None:
        return None if "x" in flags else (not _RUST_REJECTED_FLAGS.isdisjoint(flags), _PY_310)
    if group := match["group"]:
        return True, _PY_311 if group == ">" else _PY_310
    rejected = match["brace"] is not None or _exceeds(match["lower"], match["upper"])
    return rejected, _PY_311 if match["possessive"] else _PY_310


def scan_regex_syntax(pattern: str, *, read_verbose: bool = False) -> tuple[bool, tuple[int, int]] | None:
    r"""Return whether the Rust regex crate rejects a pattern's syntax, and the oldest Python that parses it.

    The scan follows the Rust grammar of every pydantic-core release from 2.3.0 on, so it never reports a
    pattern Rust accepts. Capture names such as ``(?P<ids[]>...)``, nested and POSIX character classes, braced
    escapes such as ``\p{L}``, ``\x{41}`` or ``\b{start}``, and counted repetitions with whitespace are
    consumed whole, with separate tokens inside and outside character classes. It reports backreferences and
    octal escapes, ``\N{...}``, ``\Z``, ``[\b]``, ``(?P=name)``, atomic groups, conditionals, comments, the
    ``a`` and ``L`` flags, braces that cannot start a counted repetition, counted repetitions whose minimum
    exceeds their maximum (``a{ 3 , 2 }``, which Python's ``re`` reads as text), hexadecimal escapes of a
    surrogate or of a code point past U+10FFFF (``\uD800``, which Python's ``re`` reads as that surrogate) and, in a
    pattern that is not verbose, what ``_rust_rejects_beyond_tokens`` reads: Unicode properties Rust does not
    implement, a class escape as the end of a range and counted repetitions past Rust's compiled-size limit. Atomic
    groups and possessive quantifiers need Python 3.11, and ``\z`` needs Python 3.14. Returns None for verbose
    patterns, whose whitespace and comments the scan does not model, unless ``read_verbose`` reads the ones that
    start with a global ``(?x)`` group. Their comments run to the end of the line, also inside a character class,
    where Rust then reports the class unclosed; any other flag group that mentions ``x`` still gives None.
    """
    if (tokens := _scan_tokens(pattern, read_verbose=read_verbose)) is None:
        return None
    rejected, minimum_python, depth, verbose = tokens
    return (rejected or (depth > 0 if verbose else _rust_rejects_beyond_tokens(pattern)), minimum_python)


def _scan_tokens(pattern: str, *, read_verbose: bool) -> tuple[bool, tuple[int, int], int, bool] | None:
    """Return what the token scan of ``scan_regex_syntax`` finds: rejection, oldest Python, open classes, verbosity."""
    rejected = False
    minimum_python = _PY_310
    depth = 0
    position = 0
    verbose = False
    token, class_token = _REGEX_TOKEN, _REGEX_CLASS_TOKEN
    if read_verbose and (start := _compiled(_VERBOSE_FLAGS).match(pattern)) is not None:
        verbose = True
        rejected = not _RUST_REJECTED_FLAGS.isdisjoint(start["flags"])
        position = start.end()
        token, class_token = _REGEX_VERBOSE_TOKEN, _REGEX_VERBOSE_CLASS_TOKEN
    while (match := _compiled(class_token if depth else token).search(pattern, position)) is not None:
        position = match.end()
        if depth or match["open"]:
            depth += bool(match["open"]) - bool(depth and match["close"])
            rejected |= match["escape"] in _RUST_REJECTED_CLASS_ESCAPES or _names_no_scalar_value(match["code"])
            continue
        if (requirements := _regex_token_requirements(match)) is None:
            return None
        rejected |= requirements[0]
        minimum_python = max(minimum_python, requirements[1])
    return rejected, minimum_python, depth, verbose


_ESCAPE: Final = r"(?s)\\(?:[pP](?:\{[^}]*\}|.?)|[xuU]\{|.)"
_CLASS_ESCAPES: Final = frozenset("wWdDsS")
_UNIMPLEMENTED_PROPERTIES: Final = frozenset({"alnum", "blank", "graph", "print", "xdigit", "all"})
"""The UTS #18 compatibility properties Rust's regex crate does not implement, and Java's ``all``, loosely named."""
_PYTHON_REJECTED_ESCAPES: Final = frozenset("ceghijklmopqyCEFGHIJKLMOPQRTVXY")
"""The letters Python's ``re`` reads after a backslash nowhere, on every Python from 3.10 on, ``p`` and ``P`` too."""
_BRACED_CODES: Final = frozenset({"x{", "u{", "U{"})
_PYTHON_CLASS: Final = r"(?<!\\)(?:\\\\)*\[\^?\]?(?:[^\\\]]|\\.)*\]"
_CLASS_ITEM: Final = (
    rf"(?s:{_CODE_ESCAPE}|"
    r"\\(?:x[0-9A-Fa-f]{2}|[0-3][0-7]{2}|[0-7]{1,2}|N\{[^}]*\}|[pP](?:\{[^}]*\}|.?)|.?)|.)"
)
_CLASS_PART: Final = rf"(?s)({_CLASS_ITEM})(?:-(\\.|[^\\]))?"
r"""A member of a class: an item (a character or an escape, ``\pL``, ``\p{L}``, ``\x41`` whole) or a range from it."""
_SIZE_THRESHOLDS: Final = MappingProxyType({
    "w": 218,
    "W": 233,
    "d": 2256,
    "D": 899,
    "s": 15420,
    "S": 5449,
    "dot": 10878,
    "literal": 327674,
})
r"""The copies of an atom from which Rust's compiled-size limit (10 MiB) refuses a pattern, by the kind of the atom.

Each is the largest count, required (``a{N}``) or optional (``a{0,N}``), that a pydantic-core release from 2.10 to
2.50 still refuses for one repetition of the atom alone: ``\w`` and a class of it and ASCII (``w``), ``\W``, ``\d``,
``\D``, ``\s``, ``\S``, any character (``dot``, also a negated class) and a literal or anything else (``literal``).
The scan compares each repetition with the count of its atom alone (``_SizeScan``): the reading shipped since 0.92.0,
kept for its answers. It is no bound of what Rust compiles: a class can compile to less than its escape
(``[\w&&[a-z]]{300}``, ``[^\x00-\x7F]{10900}``), and so can an alternation (``(\w|\W){300}``) or a pattern Rust
searches as literals (``a|ab\w{300}``); all of these compile. The bound is ``_CERTAIN_THRESHOLDS``.
"""
_CERTAIN_THRESHOLDS: Final = MappingProxyType({
    "w": 218,
    "W": 234,
    "d": 2275,
    "D": 904,
    "s": 17247,
    "S": 5774,
    "pL": 254,
    "PL": 251,
    "pM": 577,
    "PM": 398,
    "pN": 1133,
    "PN": 544,
    "pP": 939,
    "PP": 485,
    "pS": 692,
    "PS": 506,
    "pZ": 18280,
    "PZ": 5890,
    "pC": 251,
    "PC": 215,
    "dot": 10878,
})
r"""The copies of the smallest atom of a kind from which Rust's compiled-size limit refuses a pattern.

A kind is a class escape (``w`` is ``\w``), a major general category (``pL`` is ``\p{L}``, ``PL`` is ``\P{L}``) or
every character (``dot``). Each count is the first that a pydantic-core release from 2.0 to 2.50 refuses, the largest
over the releases, for one repetition of the smallest atom of the kind, anchored at either end or not: its class
without any ASCII character, as each ASCII range adds to a copy (``_RANGE_BYTES``), and for ``dot`` every character,
which has one. The escape alone, a class of it with ASCII characters and either under ``(?i)`` never compile to less
(measured). Rust compiles each copy anew, so the copies of repetitions, each over the count of its kind, add up
(measured for every pair of kinds on 2.0, 2.3, 2.23 and 2.50). A first refused count bounds one copy only up to the
few states of the measured pattern around it, and what a copy compiles to moves a little with where its states sit,
so their sum reports a pattern only past ``_CERTAIN_LIMIT``. An atom of no kind here has no measured least size and
adds nothing.
"""
_CASED_THRESHOLDS: Final = MappingProxyType({
    "pLu": 399,
    "pLl": 385,
    "pL pN": 229,
    "pL pN pZ": 228,
    "pL pM pN pP pS pZ": 215,
    "pL pM pN pP pS": 215,
    "d pL pM": 222,
    "pL pN pP": 217,
    "pL pM pN pP pS s": 215,
    "d pL": 243,
    "pL pN s": 228,
})
r"""More kinds of ``_CERTAIN_THRESHOLDS``, measured alike, that count only in a pattern whose flags never fold case.

The uppercase and the lowercase letters (``pLu``, ``pLl``), which ``(?i)`` turns into every cased letter, far
cheaper, and the unions of general categories and class escapes that schemas write (``pL pN`` is ``[\p{L}\p{N}]``,
named by its members): a union compiles to more than its largest member, by an amount only a measured row bounds.
"""
_LIMIT_BYTES: Final = 10 * 1024 * 1024
"""Rust's compiled-size limit in bytes, which pydantic-core keeps."""
_SIZE_LIMIT: Final = math.lcm(
    _LIMIT_BYTES, *_SIZE_THRESHOLDS.values(), *_CERTAIN_THRESHOLDS.values(), *_CASED_THRESHOLDS.values()
)
"""Rust's compiled-size limit, in a unit that makes a byte and the size of one copy of every kind a whole number."""
_RANGE_BYTES: Final = 34
r"""The bytes each ASCII range of an atom adds to one copy of it (``\w`` has four: ``0-9``, ``A-Z``, ``_``, ``a-z``).

A range is a byte-range state and an alternative of the class's union in the reverse automaton: 36 bytes on every
character, ``\s`` and ``\S`` on 2.3 to 2.50, from 34.3 on ``\p{Z}``, the least, and 64 on 2.0. Under the flag ``i``,
which folds the ASCII letters of a class, no range is counted.
"""
_OPTIONAL_BYTES: Final = 38
r"""The bytes each optional copy of a counted repetition (``a{1,5}`` has four) adds: a union state with two branches.

Measured: 40 on every character on 2.3 to 2.50, from 38.3 on ``\p{Z}``, the least, and 32 on 2.0, whose copies are
far larger than the counts of ``_CERTAIN_THRESHOLDS`` say.
"""
_CERTAIN_LIMIT: Final = _SIZE_LIMIT * 101 // 100
r"""The certain size from which a pattern passes Rust's compiled-size limit on every release: the limit and 1 %.

Measured on 2.3, 2.16 and 2.23, the releases whose counts the table holds: one copy of the cheapest class of a kind
compiles to at most 0.017 % less than the limit over its count (``\p{N}`` without its ASCII digits), and a block of
copies of half the limit to at most 0.05 % of the limit less than alone, by where its states sit. The margin is nine
times what both take from two such blocks, and stays below the 2.7 % by which the smallest pattern of #6877 passes
the limit.
"""
_MEMBER_ALIASES: Final = MappingProxyType({"pNd": "d", "PNd": "D"})
"""The members of a class that are a kind of ``_CERTAIN_THRESHOLDS`` by another name (``_member``)."""
_SIZES: Final = MappingProxyType({kind: _SIZE_LIMIT // count for kind, count in _SIZE_THRESHOLDS.items()})
"""The size one copy of an atom of a kind counts as in a repetition read alone (``_SIZE_THRESHOLDS``)."""
_CERTAIN_SIZES: Final = MappingProxyType({kind: _SIZE_LIMIT // count for kind, count in _CERTAIN_THRESHOLDS.items()})
"""The size one copy of an atom of a kind compiles to at least (``_CERTAIN_THRESHOLDS``)."""
_CASED_SIZES: Final = MappingProxyType({
    **_CERTAIN_SIZES,
    **{kind: _SIZE_LIMIT // count for kind, count in _CASED_THRESHOLDS.items()},
})
"""The least sizes of ``_CERTAIN_SIZES`` and of ``_CASED_THRESHOLDS``, in a pattern whose flags never fold case."""
_RANGE_SIZE: Final = _SIZE_LIMIT // _LIMIT_BYTES * _RANGE_BYTES
"""The size an ASCII range adds to one copy of an atom (``_RANGE_BYTES``)."""
_OPTIONAL_SIZE: Final = _SIZE_LIMIT // _LIMIT_BYTES * _OPTIONAL_BYTES
"""The size an optional copy adds to a counted repetition (``_OPTIONAL_BYTES``)."""
_ASCII: Final = (1 << 128) - 1
"""The ASCII characters as bits, bit ``n`` for code point ``n``."""
_ASCII_MEMBERS: Final = MappingProxyType({
    "w": "0-9A-Z_a-z",
    "d": "0-9",
    "s": "\t-\r ",
    "pL": "A-Za-z",
    "pM": "",
    "pN": "0-9",
    "pP": "!-#%-*,-/:;?@[-]_{}",
    "pS": "$+<->^`|~",
    "pZ": " ",
    "pC": "\x00-\x1f\x7f",
    "pLu": "A-Z",
    "pLl": "a-z",
})
"""The ASCII characters of a class escape or a general category, as a class writes them; its negation has the rest."""
_CONTROL_ESCAPES: Final = MappingProxyType({"a": 7, "t": 9, "n": 10, "v": 11, "f": 12, "r": 13})
"""The letters after a backslash that name an ASCII control character."""
_SIZE_QUANTIFIER: Final = r"\{\s*(?P<lower>[0-9]+)\s*(?:(?P<comma>,)\s*(?P<upper>[0-9]*)\s*)?\}[?+]?|[*+?][?+]?"
_ASCII_FLAG_GROUP: Final = r"\(\?[A-Za-z]*-[A-Za-z]*u"
_VERBOSE_FLAG_GROUP: Final = r"\(\?[A-Za-z-]*x"
_PYTHON_REJECTED_FLAGS: Final = r"\(\?[A-Za-z]*-[A-Za-z]*[auL]"
_SIZE_ESCAPE: Final = rf"{_ESCAPE}|\\"
"""An escape (``_ESCAPE``), or the backslash that ends a pattern alone."""
_ZERO_WIDTH_ESCAPES: Final = frozenset("bBAzZ<>")
"""The letters after a backslash of an atom that matches no character, which Rust compiles to no copy of one."""
_ZERO_WIDTH: Final = frozenset("^$")
"""The characters of an atom that matches no character outside an escape; any other one consumes a character."""
_SEQUENCE_ENDS: Final = frozenset({"", "|", ")"})
"""What ends a sequence of a pattern: its end, the next branch or the end of its group."""
_GROUP_PREFIX: Final = r"\((?:\?(?:<?[=!]|P?<[^>]*>|[A-Za-z-]*:|>))?"
_BRANCH_START: Final = r"[0-9A-Za-z]"
"""The letter or digit a branch of an alternation starts with."""
_UNCERTAIN_CLASS: Final = r"\[|&&|--|~~|\\[xuU]"
"""What may make a class smaller than its members: a nested or POSIX class, a set operation, a hexadecimal escape."""
_ANCHORED: Final = r"\A\^|(?<!\\)(?:\\\\)*\$\Z"
"""A pattern that starts with ``^`` or ends with ``$``: with an assertion in it, Rust compiles all of the pattern."""
_LITERAL_SEARCH: Final = r"\||[?*+}]\?|\(\?[A-Za-z-]*U"
r"""What may let Rust search a pattern that holds no assertion as literals alone, compiling none of it.

An alternation whose earlier branch always matches first (``a|ab\w{300}``) and a lazy quantifier, also under the flag
``U`` (``a(?:b\w{300})??``): the part that never matches is not compiled, whatever its size.
"""


def _escapes(text: str) -> Iterator[str]:
    """Yield each escape of a pattern, read from its start so that an escaped backslash escapes nothing else."""
    return (match.group() for match in _compiled(_ESCAPE).finditer(text) if match.group().startswith("\\"))


def _rust_rejects_property(escape: str) -> bool:
    r"""Return whether Rust rejects a ``\p`` escape: a name it does not implement, or one letter that is no letter."""
    if escape.startswith("{", 2):
        name = escape[3:-1].replace(" ", "").replace("_", "").replace("-", "").lower()
        return name in _UNIMPLEMENTED_PROPERTIES or name.startswith("java")
    return not escape[2:].isascii() or not escape[2:].isalpha()


def _is_class_set(item: str) -> bool:
    r"""Return whether an item of a class is a class escape or a property (``\w``, ``\pL``, ``\p{L}``)."""
    return item[1:] in _CLASS_ESCAPES or (bool(item[2:]) and item[1] in "pP")


def _class_escape_ranges(pattern: str, *, rust: bool) -> bool:
    r"""Return whether a class uses a class escape as a boundary of a range (``[\w- ]``, ``[a-\w]``) in an engine.

    A boundary is a class escape or a property, in braces (``[\p{L}-_]``) or of one letter (``[\pL-_]``). An escape of
    one character is an item, whatever its length (``\x7a``, ``\u{7a}``): ``[a-\x7a-\w]`` has one range. Two hyphens are
    a difference in Rust (``[\pL\pP--_]`` compiles), so a property of
    one letter before them starts no range; the other starts keep their answer there (``[\w--_]`` and ``[\p{L}--_]``
    compile too, and are read as ranges). A range is read from the item after the end of the last one: ``[a-c-\w]``
    has one range. Rust reads more than that: a doubled ``-``, ``&`` or ``~`` is an operation (``[a-z--\w]`` and
    ``[a&&-\w]`` have no range), the ``]`` that opens a class is no item, and a ``[`` opens a nested class, with its own
    ``^`` (``[[-\w]]``); Python's ``re`` reads each of them as a character.
    """
    for match in _compiled(_PYTHON_CLASS).finditer(pattern):
        body = match.group()
        members = body[body.index("[") + 1 : -1].removeprefix("^")
        tokens = _compiled(_CLASS_ITEM).findall(members.removeprefix("]") if rust else members)
        position = 0
        while position + 2 < len(tokens):
            start, hyphen, end = tokens[position : position + 3]
            if rust and start == hyphen and start in "-&~":
                position += 2
            elif rust and start == "[":
                position += 1 + (hyphen == "^")
            elif hyphen != "-":
                position += 1
            elif _is_class_set(end) or (
                _is_class_set(start) and (start[1:] in _CLASS_ESCAPES or start[3:] or end != "-")
            ):
                return True
            else:
                position += 3
    return False


def python_rejects(pattern: str) -> bool:
    r"""Return whether Python's ``re`` rejects a pattern on every Python from 3.10 on, decided statically.

    It rejects any ``\p`` or ``\P``, a braced hexadecimal escape (``\x{41}``), a class escape as the start of a range,
    a flag group that turns ``a``, ``u`` or ``L`` off and the letters it reads after a backslash nowhere. A verbose
    pattern is never decided, as a comment may hold any text.
    """
    if _compiled(_VERBOSE_FLAG_GROUP).search(pattern) is not None:
        return False
    return (
        _class_escape_ranges(pattern, rust=False)
        or _compiled(_PYTHON_REJECTED_FLAGS).search(pattern) is not None
        or any(escape[1] in _PYTHON_REJECTED_ESCAPES or escape[1:] in _BRACED_CODES for escape in _escapes(pattern))
    )


@cache
def _rust_rejects_beyond_tokens(pattern: str) -> bool:
    """Return whether Rust rejects a pattern that is not verbose for what the token scan does not read.

    A Unicode property Rust does not implement (``_rust_rejects_property``), a class escape as the start of a range
    and counted repetitions past the compiled-size limit (``_exceeds_compiled_size``).
    """
    return (
        any(escape[1] in "pP" and _rust_rejects_property(escape) for escape in _escapes(pattern))
        or _class_escape_ranges(pattern, rust=True)
        or _exceeds_compiled_size(pattern)
    )


class _SizeScan:
    """Read what Rust compiles for a pattern against its compiled-size limit, in units of ``_SIZE_LIMIT``.

    Each part of the pattern has three sizes: its largest atom (``_SIZES``), its largest repetition read alone, and
    its certain size, the sum of the repetitions of its atoms whose least size is measured (``_CERTAIN_SIZES``, and
    ``_CASED_SIZES`` where no flag folds case). The flags of the whole pattern decide what ``.`` matches and whether
    the ASCII ranges of an atom count (``_RANGE_BYTES``).
    """

    def __init__(self, pattern: str) -> None:
        self.pattern = pattern
        self.position = 0
        flags = {letter for group in _compiled(_FLAG_GROUP).finditer(pattern) for letter in group["flags"]}
        self.cased = "i" not in flags
        self.sizes = _CASED_SIZES if self.cased else _CERTAIN_SIZES
        self.dot = _ASCII if "s" in flags else _ASCII & ~(1 << ord("\n"))

    def _peek(self) -> str:
        return self.pattern[self.position : self.position + 1]

    def alternation(self) -> tuple[int, int, int, bool]:
        """Return the three sizes of an alternation, and whether each branch holds an atom of a kind outside a group.

        Rust compiles what every branch starts with once, so only the largest branch is certain, unless two branches
        start with different letters or digits: then no branch shares a part and their certain sizes add up.
        """
        starts: set[str] = set()
        atom = alone = largest = together = 0
        held = True
        while True:
            if (start := _compiled(_BRANCH_START).match(self.pattern, self.position)) is not None:
                starts.add(start.group().lower())
            branch_atom, branch_alone, certain, branch_held = self._sequence()
            atom = max(atom, branch_atom)
            alone = max(alone, branch_alone)
            largest = max(largest, certain)
            together += certain
            held &= branch_held
            if self._peek() != "|":
                return atom, alone, together if len(starts) > 1 else largest, held
            self.position += 1

    def _sequence(self) -> tuple[int, int, int, bool]:
        """Return the three sizes of a sequence, and whether it holds an atom of a kind outside a group.

        A counted repetition read alone is its copies of the largest atom it repeats. The certain size adds each
        group, once per copy, and each atom that a repetition copies twice or more or that another part follows,
        with each optional copy (``_OPTIONAL_BYTES``): Rust may compile a single copy that ends a branch into one class
        with the branches beside it.
        """
        atom = alone = certain = 0
        held = False
        while (character := self._peek()) not in _SEQUENCE_ENDS:
            part, part_alone, part_certain = self._atom()
            copies, counted, optional = self._quantifier()
            alone = max(alone, part_alone * copies, part * copies * counted)
            if part_certain and (character == "(" or copies > 1 or self._peek() not in _SEQUENCE_ENDS):
                certain += part_certain * copies + _OPTIONAL_SIZE * optional
            held |= character != "(" and part_certain * copies > 0
            atom = max(atom, part * bool(copies))
        return atom, alone, certain, held

    def _quantifier(self) -> tuple[int, bool, int]:
        """Return how many copies of the atom Rust compiles, whether a count asks for them, and how many are optional.

        An open-ended repetition (``a{N,}``) compiles at least its ``N`` mandatory copies.
        """
        if (match := _compiled(_SIZE_QUANTIFIER).match(self.pattern, self.position)) is None:
            return 1, False, 0
        self.position = match.end()
        if match["lower"] is None:
            return 1, False, 0
        if match["comma"] is None:
            return int(match["lower"]), True, 0
        if match["upper"]:
            return int(match["upper"]), True, int(match["upper"]) - int(match["lower"])
        return max(int(match["lower"]), 1), True, 0

    def _atom(self) -> tuple[int, int, int]:
        """Return the three sizes of one atom, none for one that matches no character.

        An atom that is no group holds no repetition, and its certain size is the least size of one copy.
        """
        character = self._peek()
        if character == "(":
            return self._group()
        if character == "[":
            end, _ = _class_end(self.pattern, self.position)
            body = self.pattern[self.position : end]
            self.position = end
            return _SIZES[_class_kind(body)], 0, self._copy(*_certain_class(body, cased=self.cased))
        if character == "\\":
            escape = cast("re.Match[str]", _compiled(_SIZE_ESCAPE).match(self.pattern, self.position)).group()
            self.position += len(escape)
            letter = escape[1:2]
            if letter in _ZERO_WIDTH_ESCAPES:
                return 0, 0, 0
            kind = _member(escape)
            return _SIZES[letter if letter in _CLASS_ESCAPES else "literal"], 0, self._copy(kind, _member_ascii(kind))
        self.position += 1
        if character in _ZERO_WIDTH:
            return 0, 0, 0
        return (_SIZES["dot"], 0, self._copy("dot", self.dot)) if character == "." else (_SIZES["literal"], 0, 0)

    def _copy(self, kind: str, characters: int) -> int:
        """Return the least size of one copy of an atom of a kind that matches the ASCII ``characters`` (bits).

        Each ASCII range counts, past the one of every character for ``dot``, where no flag folds case.
        """
        size = self.sizes.get(kind, 0)
        if size and self.cased:
            size += _RANGE_SIZE * ((characters & ~(characters << 1)).bit_count() - (kind == "dot"))
        return size

    def _group(self) -> tuple[int, int, int]:
        """Return the three sizes of a group; a group that only sets flags has none."""
        if (flags := _compiled(_FLAG_GROUP).match(self.pattern, self.position)) is not None and flags["end"] == ")":
            self.position = flags.end()
            return 0, 0, 0
        prefix = _compiled(_GROUP_PREFIX).match(self.pattern, self.position)
        self.position = cast("re.Match[str]", prefix).end()
        atom, alone, certain, _ = self.alternation()
        self.position += self._peek() == ")"
        return atom, alone, certain


def _class_kind(body: str) -> str:
    r"""Return the kind of atom a class counts as in a repetition read alone: an escape, any character, or a literal.

    The cheapest of its class escapes; a negated class counts as any character, or as ``\s`` where it holds a class
    escape; a class holding an escape and its negation (``[\s\S]``) counts as any character.
    """
    escapes = {escape[1] for escape in _escapes(body[1:-1]) if escape[1] in _CLASS_ESCAPES}
    if body.startswith("[^"):
        return "s" if escapes else "dot"
    if any(letter in escapes and letter.swapcase() in escapes for letter in "wds"):
        return "dot"
    return max(escapes, key=_SIZE_THRESHOLDS.__getitem__, default="literal")


def _member(escape: str) -> str:
    r"""Return the name of an escape as ``_CERTAIN_THRESHOLDS`` names a kind: ``w`` for ``\w``, ``pL`` for ``\p{L}``.

    The property of decimal digits and its negation (``_MEMBER_ALIASES``) are ``d`` and ``D``: Rust's ``\d`` is that
    general category.
    """
    name = escape[1:2] + escape[2:].strip("{}")
    return _MEMBER_ALIASES.get(name, name)


def _negation(kind: str) -> str:
    r"""Return the kind of the negation of a class escape or a general category: ``W`` for ``w``, ``PL`` for ``pL``."""
    return kind[:1].swapcase() + kind[1:]


def _member_ascii(name: str) -> int:
    """Return the ASCII characters of a class escape or a general category named as ``_member`` names it, as bits.

    Any other escape has none.
    """
    if name in _ASCII_MEMBERS:
        return _ascii_characters(_ASCII_MEMBERS[name])
    if (positive := _negation(name)) in _ASCII_MEMBERS:
        return _ASCII & ~_ascii_characters(_ASCII_MEMBERS[positive])
    return 0


def _class_character(part: str) -> int | None:
    """Return the code point a character of a class or its escape names; None for an escape of a set of them."""
    if len(part) == 1:
        return ord(part)
    letter = part[1:]
    if letter in _CONTROL_ESCAPES:
        return _CONTROL_ESCAPES[letter]
    return None if len(letter) != 1 or letter.isalnum() else ord(letter)


@cache
def _ascii_characters(members: str) -> int:
    """Return the ASCII characters that the members of a class hold, as bits.

    A range with an escape of a set at either end, which Rust refuses, counts its first member alone.
    """
    held = 0
    for part in _compiled(_CLASS_PART).finditer(members):
        start = _class_character(part[1])
        end = start if part[2] is None else _class_character(part[2])
        if start is None or end is None:
            held |= _member_ascii(_member(part[1]))
        else:
            held |= (1 << end + 1) - (1 << start)
    return held & _ASCII


def _certain_class(body: str, *, cased: bool) -> tuple[str, int]:
    r"""Return the kind whose least size a class certainly compiles to, or no kind, and its ASCII characters (bits).

    A class of one class escape or general category and ASCII characters is that kind, its negation if the class is
    negated (``[^\p{C}]`` is ``\P{C}``): the ASCII characters only change its ASCII ranges. A class that is not
    negated and holds a member with its negation (``[\s\S]``, ``[\P{M}\p{M}]``) is every character, and so is a
    negated class of characters alone (``[^<>]``) that leaves an ASCII character, where no flag folds case
    (``cased``). Beside ``\w``, ``\d`` adds nothing (``[\w\d]`` is ``\w``, as ``\d`` is inside ``\w``), and a class
    of ``\w`` and ``\s`` that is not negated is at least ``\w`` (measured: every release refuses ``[\w\s]`` from fewer
    copies than the count of ``w``, also without any ASCII character). A class of several members that is not negated
    is the union of ``_CASED_THRESHOLDS`` they name, if any. Any other class has no kind, as it may compile to less
    than a member: other unions (``[\S ]`` beside ``\S``, ``[\p{L}\p{C}]``), another property, a character beyond
    ASCII or what ``_UNCERTAIN_CLASS`` reads (``[\w&&[a-z]]``).
    """
    inner = body[1:-1]
    members = {_member(escape) for escape in _escapes(inner)}
    kinds = members & _CASED_SIZES.keys()
    if (
        not inner.isascii()
        or _compiled(_UNCERTAIN_CLASS).search(inner) is not None
        or any(member.startswith(("p", "P")) for member in members - kinds)
    ):
        return "", 0
    negated = inner.startswith("^")
    characters = _ascii_characters(inner[negated:]) ^ (_ASCII if negated else 0)
    if not negated and any(_negation(kind) in kinds for kind in kinds):
        return "dot", characters
    if "w" in kinds:
        kinds -= {"d"} if negated else {"d", "s"}
    if len(kinds) == 1:
        (kind,) = kinds
        return _negation(kind) if negated else kind, characters
    if negated:
        return "dot" if cased and not kinds and characters else "", characters
    return " ".join(sorted(kinds)), characters


def _exceeds_compiled_size(pattern: str) -> bool:
    r"""Return whether the counted repetitions of a pattern reach the size Rust refuses to compile.

    One repetition does, read alone, when it reaches the copies of its atom (``_SIZE_THRESHOLDS``): the copies of a
    repetition inside another multiply, and a repetition of a group counts as its cheapest atom. Several do together
    when their certain sizes pass the limit by its margin (``_CERTAIN_THRESHOLDS``, ``_CERTAIN_LIMIT``,
    ``_SizeScan``): a lower bound of what Rust compiles, which leaves a pattern that passes the limit by what it does
    not count to fail at import. It counts only where Rust compiles all of the pattern: one anchored at either end, one
    without what ``_LITERAL_SEARCH`` reads, or one whose every branch holds an atom of a kind outside any group, whose
    many characters leave no literal Rust could search alone. It does not model what Rust compiles to nothing: a
    repetition of no copy (``\w{120}\w{120}{0}``) and a part that matches nothing (``x(?:[^\s\S]\w{120}\w{120})?``).
    A pattern with a group that turns Unicode off, where ``\w`` and ``.`` are ASCII, is not read.
    """
    if _compiled(_ASCII_FLAG_GROUP).search(pattern) is not None:
        return False
    _, alone, certain, held = _SizeScan(pattern).alternation()
    if (
        not held
        and _compiled(_ANCHORED).search(pattern) is None
        and _compiled(_LITERAL_SEARCH).search(pattern) is not None
    ):
        certain = 0
    return alone >= _SIZE_LIMIT or certain >= _CERTAIN_LIMIT


def pattern_engine(pattern: str, *, row: EngineRow, compiled: bool = True) -> PatternEngine | None:
    """Return the engine a pattern asks of the config that builds it, decided statically.

    A lookaround always needs Python's ``re`` (the ``python-re`` engine), and so does syntax the Rust engine rejects,
    whichever Python reads the pattern (#5636): Rust fails at import on every Python, and ``python-re`` works on the
    ones that parse the syntax. Row ``adapter`` (a schema validator's adapter, 0.87.0) also selects it for a pattern
    the scan cannot read, which includes a verbose pattern. Row ``model`` (the config of a model, or of the models
    that use a type alias, 0.84.0) reads a verbose pattern that starts with ``(?x)`` and keeps the default engine for
    any other one; ``compiled`` tells whether pydantic compiles the pattern (that of a string type), and a pattern it
    does not compile asks for no engine (None) unless it holds a lookaround. A pattern that holds a lone surrogate
    (``holds_lone_surrogate``), which only Python's ``re`` reads as written (#6320), asks for ``python-re`` only where
    the config builds no pattern that the default engine runs as written (``selects_python_engine``): pydantic-core's
    default engine replaces it by U+FFFD on its way to Rust, and no Pydantic builds a ``Field`` with a compiled pattern
    that holds one.
    """
    if has_lookaround(pattern):
        return "python"
    if row == "model" and not compiled:
        return None
    if row == "adapter":
        rejected = (scan := scan_regex_syntax(pattern)) is None or scan[0]
    else:
        rejected = (scan := scan_regex_syntax(pattern, read_verbose=True)) is not None and scan[0]
    if rejected:
        return "python"
    return "surrogate" if holds_lone_surrogate(pattern) else "default"


def _rust_rejects(pattern: str) -> bool:
    """Return whether Rust rejects a pattern for certain: a lookaround, syntax the scan reports, or a class left open.

    A verbose pattern is never decided.
    """
    return has_lookaround(pattern) or (
        (scan := scan_regex_syntax(pattern)) is not None
        and (
            scan[0] or cast("tuple[bool, tuple[int, int], int, bool]", _scan_tokens(pattern, read_verbose=False))[2] > 0
        )
    )


def _escaped_class_brackets(pattern: str) -> str | None:
    r"""Return a pattern with each ``[`` inside a class escaped, where only that makes Rust read it as Python does.

    Rust reads a ``[`` inside a class as a nested class, Python's ``re`` and ECMA-262 as the character, so Rust leaves
    ``[[A-Z]`` open and refuses it. Escaped (``[\[A-Z]``), every engine reads the class alike. None where Rust reads
    the pattern as written, where the escaped pattern is still read otherwise, or where Python cannot read it.
    """
    escaped = _compiled(_PYTHON_CLASS).sub(
        lambda match: (
            match.group()[: match.group().index("[") + 1]
            + _compiled(r"(?s)(\\.)|\[").sub(
                lambda inner: inner.group(1) or "\\[", match.group()[match.group().index("[") + 1 :]
            )
        ),
        pattern,
    )
    if escaped == pattern or not _rust_rejects(pattern) or _rust_rejects(escaped) or not _reads_alike(escaped):
        return None
    return escaped


@cache
def read_pattern(pattern: str, engines: frozenset[str]) -> str | None:
    """Return the pattern an output whose models compile patterns on ``engines`` writes for a schema's ``pattern``.

    The engines, at least one, are ``python`` (Python's ``re``) and ``rust`` (pydantic-core's default, beside which
    Pydantic selects ``python-re``). A pattern no engine of the output compiles is left out (None): one that Python's
    ``re`` rejects (``python_rejects``) where Rust rejects it too or is not an engine. Where Rust is the default
    engine, a ``[`` inside a class that leaves the class open for Rust is escaped (``_escaped_class_brackets``).
    Otherwise the pattern is written as the schema writes it. The decision reads the text alone, never an installed
    engine.
    """
    if python_rejects(pattern) and ("rust" not in engines or _rust_rejects(pattern)):
        return None
    if "rust" in engines and (escaped := _escaped_class_brackets(pattern)) is not None:
        return escaped
    return pattern


def selects_python_engine(engines: Iterable[PatternEngine | None], *, surrogates: bool = True) -> bool:
    """Return whether a config selects ``python-re`` for the patterns it builds, given what each asks of it.

    A pattern whose syntax needs the engine selects it. With ``surrogates``, so does a lone surrogate (#6320) where
    every other pattern the config builds holds one too or needs the engine: the default engine reads the surrogate
    as U+FFFD, and switching would change how Python reads another pattern or refuse it at import, so a config that
    builds one the default engine runs as written keeps that engine.
    """
    asked: set[PatternEngine | None] = set()
    for engine in engines:
        if engine == "python":
            return True
        asked.add(engine)
    return surrogates and "surrogate" in asked and "default" not in asked


def control_escapes_keep_meaning(pattern: str) -> bool:
    r"""Return whether a pattern means the same when each control character in it is written as its escape.

    A regex reads ``\t``, ``\n``, ``\f`` and ``\r`` as the character each names, so a pattern may spell them that
    way, but not where the escape would mean another pattern: after a backslash, where the character is what the
    backslash escapes and its escape is a backslash and a letter, and in a verbose pattern, where the character is
    ignored whitespace or ends a comment and its escape matches it. A backspace has no escape that a regex reads as
    it: ``\b`` is a word boundary, and Rust rejects it in a class. The decision reads the text alone, so the engine
    and the Python that read the pattern do not matter.
    """
    if "\x08" in pattern:
        return False
    if _WHITESPACE_CONTROLS.isdisjoint(pattern):
        return True
    return scan_regex_syntax(pattern) is not None and _compiled(_ESCAPED_WHITESPACE_CONTROL).search(pattern) is None


def is_portable_pattern(pattern: str) -> bool:
    """Return whether a pattern runs on the default regex engine of every supported pydantic (row ``adapter``)."""
    return pattern_engine(pattern, row="adapter") == "default"


_REGEX_META_CHARACTERS: Final = frozenset(r"\.^$*+?{}[]|()")
_HEAD_CHARACTER: Final = r"[^\\.^$*+?{}\[\]|()]|\\[\\.^$*+?{}\[\]|()]"
_LITERAL_PATTERN: Final = rf"(?:{_HEAD_CHARACTER})*"
_QUANTIFIER_STARTS: Final = frozenset("*+?{")
_SINGLE_CLASS: Final = r"(?s)\\[dDwWsSntrfv]|\\x[0-9A-Fa-f]{2}|\[\^?\]?(?:[^\\\[\]]|\\.)*\]"
_PLAIN_REPETITION: Final = r"\{[0-9]+(?:,[0-9]*)?\}"
_NON_ASCII_CLASS_ESCAPE: Final = r"(?<!\\)(?:\\\\)*\\[sSwWdDbB]"
_GROUP_NAME: Final = r"\(\?(?P<python>P?)<(?![=!])(?P<name>[^>()\\]*)>"
_FLAG_GROUP: Final = r"\(\?(?P<flags>[A-Za-z-]*)(?P<end>[:)])"
_SHARED_FLAGS: Final = frozenset("imsxu-")
_BRACED_ESCAPE_LETTERS: Final = frozenset("pPxuUbB")
_PYTHON_ESCAPE_DIFFERENCES: Final = frozenset("pP<>z")
_CLASS_SET_OPERATIONS: Final = ("&&", "--", "~~")
_PLAIN_ASCII_CONTROLS: Final = frozenset("\t\n\x0b\x0c\r")
_WHITESPACE_CONTROLS: Final = frozenset("\t\n\x0c\r")
_ESCAPED_WHITESPACE_CONTROL: Final = r"(?<!\\)(?:\\\\)*\\[\t\n\x0c\r]"


def literal_pattern_value(pattern: str) -> str | None:
    """Return the string a pattern of plain characters and escaped regex metacharacters only searches for."""
    if _REGEX_META_CHARACTERS.isdisjoint(pattern):
        return pattern
    if _compiled(_LITERAL_PATTERN).fullmatch(pattern) is None:
        return None
    return re.sub(r"\\(.)", r"\1", pattern)


@final
@record
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
    if has_lookaround(pattern) or scan_regex_syntax(pattern) != (False, _PY_310):
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
@record
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
    literals = [literal_pattern_value(pattern) for pattern in patterns]
    if all(literal is not None for literal in literals):
        longest = max((literal for literal in literals if literal is not None), key=str.__len__)
        if any(literal is not None and not longest.startswith(literal) for literal in literals):
            return r"\A" + "".join(rf"(?=[\s\S]*{pattern})" for pattern in dict.fromkeys(patterns))
    if len(patterns) <= 2:  # noqa: PLR2004
        return pairwise_patterns(patterns)
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
