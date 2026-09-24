"""ECMA-262 Unicode-mode patterns planned in pure Python and matched through RE2."""

from __future__ import annotations

import string
from dataclasses import dataclass
from functools import lru_cache
from importlib import import_module
from typing import Final, Literal, Protocol

from .errors import CodecConfigurationError, CodecResourceLimitError

MAX_SOURCE_BYTES: Final = 4096
MAX_GROUP_DEPTH: Final = 32
MAX_REPEAT: Final = 1000
MAX_PROGRAM_SIZE: Final = 4096
MAX_MEMORY: Final = 1 << 20
MAX_SUBJECT_BYTES: Final = 1 << 20
MAX_TOTAL_SUBJECT_BYTES: Final = 32 << 20
CACHE_SIZE: Final = 128

PatternDialect = Literal["ecma262-u"]
Ranges = tuple[tuple[int, int], ...]

_SYNTAX_CHARACTERS: Final = frozenset("^$\\.*+?()[]{}|/")
_CONTROL_ESCAPES: Final = {"f": 0x0C, "n": 0x0A, "r": 0x0D, "t": 0x09, "v": 0x0B}
_UNIVERSE: Final[Ranges] = ((0, 0xD7FF), (0xE000, 0x10FFFF))
_DIGITS: Final[Ranges] = ((0x30, 0x39),)
_WORD: Final[Ranges] = ((0x30, 0x39), (0x41, 0x5A), (0x5F, 0x5F), (0x61, 0x7A))
_SPACE: Final[Ranges] = (
    (0x09, 0x0D),
    (0x20, 0x20),
    (0xA0, 0xA0),
    (0x1680, 0x1680),
    (0x2000, 0x200A),
    (0x2028, 0x2029),
    (0x202F, 0x202F),
    (0x205F, 0x205F),
    (0x3000, 0x3000),
    (0xFEFF, 0xFEFF),
)
_LINE_TERMINATORS: Final[Ranges] = ((0x0A, 0x0A), (0x0D, 0x0D), (0x2028, 0x2029))
_SURROGATES: Final = range(0xD800, 0xE000)
_HIGH_SURROGATES: Final = range(0xD800, 0xDC00)
_LOW_SURROGATES: Final = range(0xDC00, 0xE000)
_SUPPLEMENTARY: Final = 0x10000
_SURROGATE_BITS: Final = 10
_HEX_DIGITS: Final = frozenset(string.hexdigits)
_ADAPTER_ESCAPES: Final = frozenset("pPkc" + string.digits)


class PatternDialectError(ValueError):
    """Report syntax outside the builtin grammar, requiring an explicit schema adapter."""


class PatternResourceError(ValueError):
    """Report a pattern whose static source, depth, or repeat bound exceeds its limit."""


@dataclass(frozen=True, slots=True)
class PatternPlan:
    """Keep the original source beside its RE2 translation, without compiling it."""

    source: str
    dialect: PatternDialect
    re2_source: str


def _merge(ranges: list[tuple[int, int]]) -> Ranges:
    merged: list[tuple[int, int]] = []
    for start, end in sorted(ranges):
        if merged and start <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return tuple(merged)


def _complement(ranges: Ranges) -> Ranges:
    result: list[tuple[int, int]] = []
    for low, high in _UNIVERSE:
        cursor = low
        for start, end in ranges:
            if end < cursor or start > high:
                continue
            if start > cursor:
                result.append((cursor, start - 1))
            cursor = max(cursor, end + 1)
        if cursor <= high:
            result.append((cursor, high))
    return tuple(result)


def _class_text(ranges: Ranges) -> str:
    if not ranges:
        return r"(?:\z.)"
    if ranges == _UNIVERSE:
        return "(?s:.)"
    body = "".join(f"\\x{{{start:X}}}" if start == end else f"\\x{{{start:X}}}-\\x{{{end:X}}}" for start, end in ranges)
    return f"[{body}]"


_CLASS_ESCAPES: Final[dict[str, Ranges]] = {
    "d": _DIGITS,
    "D": _complement(_DIGITS),
    "w": _WORD,
    "W": _complement(_WORD),
    "s": _SPACE,
    "S": _complement(_SPACE),
}
_DOT: Final = _class_text(_complement(_LINE_TERMINATORS))


class _Parser:
    def __init__(self, source: str) -> None:
        self.source = source
        self.index = 0
        self.depth = 0

    def error(self, *, message: str) -> PatternDialectError:
        return PatternDialectError(f"{message} at offset {self.index}")

    def peek(self, offset: int = 0) -> str | None:
        return self.source[position] if (position := self.index + offset) < len(self.source) else None

    def parse(self) -> str:
        translated = self.disjunction(top=True)
        if self.index < len(self.source):
            raise self.error(message="Unmatched ')'")
        return translated

    def disjunction(self, *, top: bool) -> str:
        alternatives = [self.alternative(top=top)]
        while self.peek() == "|":
            self.index += 1
            alternatives.append(self.alternative(top=top))
        return "|".join(alternatives)

    def alternative(self, *, top: bool) -> str:
        terms: list[str] = []
        while (char := self.peek()) is not None and char not in "|)":
            match char:
                case "^":
                    if not top or terms:
                        raise self.error(message="'^' is only supported at the start of a top-level branch")
                    self.index += 1
                    terms.append(r"\A")
                    self.reject_quantifier()
                case "$":
                    self.index += 1
                    if not top or self.peek() not in {None, "|"}:
                        raise self.error(message="'$' is only supported at the end of a top-level branch")
                    terms.append(r"\z")
                case _:
                    atom, quantifiable = self.atom()
                    if not quantifiable:
                        terms.append(atom)
                        self.reject_quantifier()
                    elif (quantifier := self.quantifier()) is None:
                        terms.append(atom)
                    else:
                        terms.append(f"(?:{atom}){quantifier}")
        return "".join(terms)

    def reject_quantifier(self) -> None:
        if self.peek() in {"*", "+", "?", "{"}:
            raise self.error(message="An assertion cannot be quantified")

    def quantifier(self) -> str | None:
        quantifier: str
        match self.peek():
            case "*" | "+" | "?" as char:
                self.index += 1
                quantifier = char
            case "{":
                quantifier = self.bounds()
            case _:
                return None
        if self.peek() == "?":
            self.index += 1
            return f"{quantifier}?"
        return quantifier

    def number(self) -> int:
        start = self.index
        while (char := self.peek()) is not None and char.isascii() and char.isdigit():
            self.index += 1
        if start == self.index:
            raise self.error(message="Invalid quantifier bounds")
        if (value := int(self.source[start : self.index])) > MAX_REPEAT:
            msg = f"Repeat bound {value} exceeds {MAX_REPEAT}"
            raise PatternResourceError(msg)
        return value

    def bounds(self) -> str:
        self.index += 1
        low = self.number()
        high: int | None = low
        if self.peek() == ",":
            self.index += 1
            high = None if self.peek() == "}" else self.number()
        if self.peek() != "}":
            raise self.error(message="Invalid quantifier bounds")
        self.index += 1
        if high is not None and high < low:
            raise self.error(message="Quantifier bounds are out of order")
        return f"{{{low}}}" if high == low else f"{{{low},}}" if high is None else f"{{{low},{high}}}"

    def atom(self) -> tuple[str, bool]:
        char = self.source[self.index]
        match char:
            case ".":
                self.index += 1
                return _DOT, True
            case "(":
                return self.group(), True
            case "[":
                return self.character_class(), True
            case "\\":
                return self.escape()
            case "*" | "+" | "?" | "{" | "}" | "]":
                raise self.error(message="Nothing to repeat or an unescaped syntax character")
            case _:
                self.index += 1
                code = self.literal(char)
                return _class_text(((code, code),)), True

    def literal(self, char: str) -> int:
        if (code := ord(char)) in _SURROGATES:
            raise self.error(message="Lone surrogates are not supported")
        return code

    def group(self) -> str:
        self.index += 1
        if self.peek() == "?":
            if self.peek(1) != ":":
                raise self.error(message="Lookaround, named groups, and flags require a schema adapter")
            self.index += 2
        self.depth += 1
        if self.depth > MAX_GROUP_DEPTH:
            msg = f"Group depth exceeds {MAX_GROUP_DEPTH}"
            raise PatternResourceError(msg)
        inner = self.disjunction(top=False)
        if self.peek() != ")":
            raise self.error(message="Unterminated group")
        self.index += 1
        self.depth -= 1
        return f"(?:{inner})"

    def escape(self) -> tuple[str, bool]:
        self.index += 1
        if (char := self.peek()) is None:
            raise self.error(message="A pattern cannot end with '\\'")
        if char in {"b", "B"}:
            self.index += 1
            return f"\\{char}", False
        if (ranges := _CLASS_ESCAPES.get(char)) is not None:
            self.index += 1
            return _class_text(ranges), True
        code = self.character_escape(char)
        return _class_text(((code, code),)), True

    def character_escape(self, char: str) -> int:
        self.index += 1
        if (code := _CONTROL_ESCAPES.get(char)) is not None:
            return code
        match char:
            case "x":
                return self.hex_digits(2)
            case "u":
                return self.unicode_escape()
            case _ if char in _SYNTAX_CHARACTERS:
                return ord(char)
            case _ if char in _ADAPTER_ESCAPES:
                self.index -= 1
                raise self.error(message="Property, back-reference, control, and NUL escapes require a schema adapter")
            case _:
                self.index -= 1
                raise self.error(message="Unsupported identity escape")

    def hex_digits(self, count: int) -> int:
        digits = self.source[self.index : self.index + count]
        if len(digits) != count or not all(char in _HEX_DIGITS for char in digits):
            raise self.error(message="Invalid hexadecimal escape")
        self.index += count
        return int(digits, 16)

    def unicode_escape(self) -> int:
        if (code := self.hex_digits(4)) not in _SURROGATES:
            return code
        if code in _HIGH_SURROGATES and self.source[self.index : self.index + 2] == "\\u":
            self.index += 2
            if (low := self.hex_digits(4)) in _LOW_SURROGATES:
                return (
                    _SUPPLEMENTARY + ((code - _HIGH_SURROGATES.start) << _SURROGATE_BITS) + low - _LOW_SURROGATES.start
                )
        raise self.error(message="Lone surrogates are not supported")

    def class_atom(self) -> int | Ranges:
        char = self.source[self.index]
        if char != "\\":
            self.index += 1
            return self.literal(char)
        self.index += 1
        if (escaped := self.peek()) is None:
            raise self.error(message="A pattern cannot end with '\\'")
        match escaped:
            case "b":
                self.index += 1
                return 0x08
            case "-":
                self.index += 1
                return ord("-")
            case "B":
                raise self.error(message="'\\B' is not allowed in a character class")
            case _ if (ranges := _CLASS_ESCAPES.get(escaped)) is not None:
                self.index += 1
                return ranges
            case _:
                return self.character_escape(escaped)

    def character_class(self) -> str:
        self.index += 1
        negated = self.peek() == "^"
        self.index += negated
        ranges: list[tuple[int, int]] = []
        while (char := self.peek()) != "]":
            if char is None:
                raise self.error(message="Unterminated character class")
            start = self.class_atom()
            if self.peek() == "-" and self.peek(1) not in {None, "]"}:
                self.index += 1
                end = self.class_atom()
                if not isinstance(start, int) or not isinstance(end, int):
                    raise self.error(message="A class escape cannot bound a range")
                if start > end:
                    raise self.error(message="Character class range is out of order")
                ranges.append((start, end))
            elif isinstance(start, int):
                ranges.append((start, start))
            else:
                ranges.extend(start)
        self.index += 1
        allowed = _complement(_merge(ranges))
        return _class_text(allowed if negated else _complement(allowed))


def plan_pattern(source: str, dialect: PatternDialect = "ecma262-u") -> PatternPlan:
    """Check a pattern's static limits and grammar, translating it without importing RE2."""
    if len(source.encode("utf-8", "surrogatepass")) > MAX_SOURCE_BYTES:
        msg = f"Pattern source exceeds {MAX_SOURCE_BYTES} UTF-8 bytes"
        raise PatternResourceError(msg)
    return PatternPlan(source, dialect, _Parser(source).parse())


class CompiledPattern(Protocol):
    """Structural view of the RE2 search entry point used by the matcher."""

    @property
    def programsize(self) -> int:
        """Return the compiled program size."""
        ...

    def search(self, text: str, /) -> object | None:
        """Search the subject."""
        ...


@lru_cache(maxsize=CACHE_SIZE)
def compile_pattern(re2_source: str) -> CompiledPattern:
    """Compile one planned translation within the program-size and memory limits."""
    re2 = import_module("re2")
    options = re2.Options()
    options.max_mem = MAX_MEMORY
    options.never_capture = True
    options.log_errors = False
    try:
        compiled: CompiledPattern = re2.compile(re2_source, options)
    except re2.error as error:
        msg = "RE2 rejected a planned pattern"
        raise CodecConfigurationError(msg) from error
    if compiled.programsize > MAX_PROGRAM_SIZE:
        msg = f"A compiled pattern exceeds the program size limit of {MAX_PROGRAM_SIZE}"
        raise CodecConfigurationError(msg)
    return compiled


@dataclass(slots=True)
class MatchBudget:
    """Track the cumulative subject bytes one codec call may pass to matchers."""

    remaining: int = MAX_TOTAL_SUBJECT_BYTES
    subject_limit: int = MAX_SUBJECT_BYTES

    def charge(self, subject: str) -> None:
        """Reserve one subject's UTF-8 bytes or fail before matching."""
        size = len(subject) if subject.isascii() else len(subject.encode())
        if size > self.subject_limit:
            msg = f"A pattern subject exceeds {self.subject_limit} bytes"
            raise CodecResourceLimitError(msg)
        if size > self.remaining:
            msg = "Pattern subjects exceed the cumulative byte limit for one codec call"
            raise CodecResourceLimitError(msg)
        self.remaining -= size


def search(plan: PatternPlan, subject: str, budget: MatchBudget) -> bool:
    """Return whether the planned pattern occurs in the subject, charging the call budget."""
    budget.charge(subject)
    return compile_pattern(plan.re2_source).search(subject) is not None
