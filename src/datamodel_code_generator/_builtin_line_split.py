"""Lay out one logical line of generated Python as black's line splitting lays it out.

A port of black's right-hand split, delimiter split and optional-parentheses rules for the simple statements dcg
generates: assignments, annotated assignments, expression statements, class headers and decorators. The tree facts
black reads from its parse tree (what a bracket opens, which operators are unary, which commas separate arguments)
are read from the tokens instead. A line the port does not cover (comments, multi-line strings, backslash
continuations, slices, power operators, lambdas with parameters) gives ``None`` and is left as it is.
"""

from __future__ import annotations

import ast
import keyword
import re
import token
import tokenize
from bisect import bisect_right
from dataclasses import dataclass, field
from enum import Enum, auto
from functools import cache, partial
from itertools import takewhile
from typing import TYPE_CHECKING, Final

from datamodel_code_generator._python_decorator import string_token_spans
from datamodel_code_generator._target_unicode import NEWEST_TARGET, string_repr

if TYPE_CHECKING:
    from collections.abc import Callable, Collection, Iterable, Iterator, Sequence

COMPREHENSION_PRIORITY: Final = 20
COMMA_PRIORITY: Final = 18
TERNARY_PRIORITY: Final = 16
LOGIC_PRIORITY: Final = 14
STRING_PRIORITY: Final = 12
COMPARATOR_PRIORITY: Final = 10
DOT_PRIORITY: Final = 1
MATH_PRIORITIES: Final = {
    token.VBAR: 9,
    token.CIRCUMFLEX: 8,
    token.AMPER: 7,
    token.LEFTSHIFT: 6,
    token.RIGHTSHIFT: 6,
    token.PLUS: 5,
    token.MINUS: 5,
    token.STAR: 4,
    token.SLASH: 4,
    token.DOUBLESLASH: 4,
    token.PERCENT: 4,
    token.AT: 4,
    token.TILDE: 3,
    token.DOUBLESTAR: 2,
}
COMPARATORS: Final = frozenset({
    token.LESS,
    token.GREATER,
    token.EQEQUAL,
    token.NOTEQUAL,
    token.LESSEQUAL,
    token.GREATEREQUAL,
})
BRACKET: Final = {token.LPAR: token.RPAR, token.LSQB: token.RSQB, token.LBRACE: token.RBRACE}
OPENING_BRACKETS: Final = frozenset(BRACKET)
CLOSING_BRACKETS: Final = frozenset(BRACKET.values())
BRACKETS: Final = OPENING_BRACKETS | CLOSING_BRACKETS
_UNARY_CANDIDATES: Final = frozenset({token.MINUS, token.PLUS, token.TILDE, token.STAR, token.DOUBLESTAR, token.AT})
_VALUE_KEYWORDS: Final = frozenset({"True", "False", "None"})
_AUGMENTED_ASSIGNMENTS: Final = frozenset({
    token.PLUSEQUAL,
    token.MINEQUAL,
    token.STAREQUAL,
    token.ATEQUAL,
    token.SLASHEQUAL,
    token.PERCENTEQUAL,
    token.AMPEREQUAL,
    token.VBAREQUAL,
    token.CIRCUMFLEXEQUAL,
    token.LEFTSHIFTEQUAL,
    token.RIGHTSHIFTEQUAL,
    token.DOUBLESTAREQUAL,
    token.DOUBLESLASHEQUAL,
})


class CannotSplitError(Exception):
    """A transform could not split the line (black's ``CannotTransform``)."""


class _Kind(Enum):
    """What a bracket opens, as black reads it from the parent node."""

    ATOM = auto()
    TRAILER = auto()
    CLASSDEF = auto()
    PARAMETERS = auto()


class _Feature(Enum):
    FORCE_OPTIONAL_PARENTHESES = auto()


class _Component(Enum):
    HEAD = auto()
    BODY = auto()
    TAIL = auto()


class _Leaf:
    """One token of the line; brackets and depth are marked as black marks its leaves."""

    __slots__ = (
        "annotation",
        "arglist",
        "bracket_depth",
        "comparator",
        "kind",
        "logic",
        "opening_bracket",
        "parameter_default",
        "prefix",
        "priority_name",
        "synthetic",
        "type",
        "unary",
        "value",
        "whitespace",
    )

    def __init__(self, type_: int, value: str) -> None:
        self.type = type_
        self.value = value
        self.prefix = ""
        self.whitespace = ""
        self.kind = _Kind.ATOM
        self.arglist = False
        self.annotation = False
        self.unary = False
        self.comparator = False
        self.logic = False
        self.priority_name = 0
        self.synthetic = False
        self.parameter_default = False
        self.opening_bracket: _Leaf | None = None
        self.bracket_depth = 0

    def copy(self) -> _Leaf:
        """Return a fresh leaf with this leaf's facts and its own whitespace as prefix (black's ``append_leaves``)."""
        leaf = _Leaf(self.type, self.value)
        leaf.whitespace = self.whitespace
        leaf.kind = self.kind
        leaf.arglist = self.arglist
        leaf.annotation = self.annotation
        leaf.unary = self.unary
        leaf.comparator = self.comparator
        leaf.logic = self.logic
        leaf.priority_name = self.priority_name
        leaf.synthetic = self.synthetic
        leaf.parameter_default = self.parameter_default
        return leaf

    def __str__(self) -> str:
        return f"{self.prefix}{self.value}"


def _comma() -> _Leaf:
    leaf = _Leaf(token.COMMA, ",")
    leaf.synthetic = True
    return leaf


# The ranges black counts as two columns wide: black's WIDTH_TABLE (wcwidth 0.2.14, Unicode 17.0.0). A static
# table keeps the width, and so the layout, the same on every Python: the running Python's unicodedata is as old as
# that Python (3.11 calls the then-unassigned U+1E4D0 full width).
_WIDE_RANGES: tuple[tuple[int, int], ...] = (
    (4352, 4447),
    (8986, 8987),
    (9001, 9002),
    (9193, 9196),
    (9200, 9200),
    (9203, 9203),
    (9725, 9726),
    (9748, 9749),
    (9776, 9783),
    (9800, 9811),
    (9855, 9855),
    (9866, 9871),
    (9875, 9875),
    (9889, 9889),
    (9898, 9899),
    (9917, 9918),
    (9924, 9925),
    (9934, 9934),
    (9940, 9940),
    (9962, 9962),
    (9970, 9971),
    (9973, 9973),
    (9978, 9978),
    (9981, 9981),
    (9989, 9989),
    (9994, 9995),
    (10024, 10024),
    (10060, 10060),
    (10062, 10062),
    (10067, 10069),
    (10071, 10071),
    (10133, 10135),
    (10160, 10160),
    (10175, 10175),
    (11035, 11036),
    (11088, 11088),
    (11093, 11093),
    (11904, 11929),
    (11931, 12019),
    (12032, 12245),
    (12272, 12329),
    (12336, 12350),
    (12353, 12438),
    (12443, 12543),
    (12549, 12591),
    (12593, 12686),
    (12688, 12773),
    (12783, 12830),
    (12832, 12871),
    (12880, 42124),
    (42128, 42182),
    (43360, 43388),
    (44032, 55203),
    (63744, 64255),
    (65040, 65049),
    (65072, 65106),
    (65108, 65126),
    (65128, 65131),
    (65281, 65376),
    (65504, 65510),
    (94176, 94179),
    (94194, 94198),
    (94208, 101589),
    (101631, 101662),
    (101760, 101874),
    (110576, 110579),
    (110581, 110587),
    (110589, 110590),
    (110592, 110882),
    (110898, 110898),
    (110928, 110930),
    (110933, 110933),
    (110948, 110951),
    (110960, 111355),
    (119552, 119638),
    (119648, 119670),
    (126980, 126980),
    (127183, 127183),
    (127374, 127374),
    (127377, 127386),
    (127488, 127490),
    (127504, 127547),
    (127552, 127560),
    (127568, 127569),
    (127584, 127589),
    (127744, 127776),
    (127789, 127797),
    (127799, 127868),
    (127870, 127891),
    (127904, 127946),
    (127951, 127955),
    (127968, 127984),
    (127988, 127988),
    (127992, 127994),
    (128000, 128062),
    (128064, 128064),
    (128066, 128252),
    (128255, 128317),
    (128331, 128334),
    (128336, 128359),
    (128378, 128378),
    (128405, 128406),
    (128420, 128420),
    (128507, 128591),
    (128640, 128709),
    (128716, 128716),
    (128720, 128722),
    (128725, 128728),
    (128732, 128735),
    (128747, 128748),
    (128756, 128764),
    (128992, 129003),
    (129008, 129008),
    (129292, 129338),
    (129340, 129349),
    (129351, 129535),
    (129648, 129660),
    (129664, 129674),
    (129678, 129734),
    (129736, 129736),
    (129741, 129756),
    (129759, 129770),
    (129775, 129784),
    (131072, 196605),
    (196608, 262141),
)
_WIDE_STARTS = tuple(start for start, _ in _WIDE_RANGES)


@cache
def _char_width(char: str) -> int:
    index = bisect_right(_WIDE_STARTS, codepoint := ord(char)) - 1
    return 2 if index >= 0 and codepoint <= _WIDE_RANGES[index][1] else 1


def str_width(text: str) -> int:
    """Return the display width black measures a line with: wide East Asian characters count twice.

    Measure text with its lent letters given back (``_Mode.restore``): a lent letter can be wide where the character it
    stands for is not.
    """
    return len(text) if text.isascii() else sum(map(_char_width, text))


def _is_split_before_delimiter(leaf: _Leaf, previous: _Leaf | None) -> int:  # noqa: PLR0911
    if leaf.unary:
        return 0
    if leaf.type == token.DOT and (previous is None or previous.type in CLOSING_BRACKETS):
        return DOT_PRIORITY
    if leaf.type in MATH_PRIORITIES:
        return MATH_PRIORITIES[leaf.type]
    if leaf.type in COMPARATORS or leaf.comparator:
        return COMPARATOR_PRIORITY
    if leaf.type == token.STRING and previous is not None and previous.type == token.STRING:
        return STRING_PRIORITY
    if leaf.priority_name:
        return leaf.priority_name
    return LOGIC_PRIORITY if leaf.logic else 0


@dataclass
class _BracketTracker:
    depth: int = 0
    bracket_match: dict[tuple[int, int], _Leaf] = field(default_factory=dict)
    delimiters: dict[int, int] = field(default_factory=dict)
    previous: _Leaf | None = None
    for_loop_depths: list[int] = field(default_factory=list)
    lambda_argument_depths: list[int] = field(default_factory=list)
    invisible: list[_Leaf] = field(default_factory=list)

    def mark(self, leaf: _Leaf) -> None:
        if self.for_loop_depths and self.for_loop_depths[-1] == self.depth and _is_name(leaf, "in"):
            self.depth -= 1
            self.for_loop_depths.pop()
        if self.lambda_argument_depths and self.lambda_argument_depths[-1] == self.depth and leaf.type == token.COLON:
            self.depth -= 1
            self.lambda_argument_depths.pop()
        if leaf.type in CLOSING_BRACKETS:
            self.depth -= 1
            leaf.opening_bracket = self.bracket_match.pop((self.depth, leaf.type))
            if not leaf.value:
                self.invisible.append(leaf)
        leaf.bracket_depth = self.depth
        if self.depth == 0:
            if (delimiter := _is_split_before_delimiter(leaf, self.previous)) and self.previous is not None:
                self.delimiters[id(self.previous)] = delimiter
            elif leaf.type == token.COMMA:
                self.delimiters[id(leaf)] = COMMA_PRIORITY
        if leaf.type in OPENING_BRACKETS:
            self.bracket_match[self.depth, BRACKET[leaf.type]] = leaf
            self.depth += 1
            if not leaf.value:
                self.invisible.append(leaf)
        self.previous = leaf
        if _is_name(leaf, "lambda"):
            self.depth += 1
            self.lambda_argument_depths.append(self.depth)
        if _is_name(leaf, "for"):
            self.depth += 1
            self.for_loop_depths.append(self.depth)

    def max_delimiter_priority(self, exclude: Collection[int] = ()) -> int:
        return max(priority for leaf_id, priority in self.delimiters.items() if leaf_id not in exclude)

    def delimiter_count_with_priority(self, priority: int) -> int:
        return sum(1 for value in self.delimiters.values() if value == priority)


def _is_name(leaf: _Leaf, value: str) -> bool:
    return leaf.type == token.NAME and leaf.value == value


@dataclass
class _Line:
    depth: int = 0
    leaves: list[_Leaf] = field(default_factory=list)
    bracket_tracker: _BracketTracker = field(default_factory=_BracketTracker)
    inside_brackets: bool = False
    should_split_rhs: bool = False
    magic_trailing_comma: _Leaf | None = None
    split_priority: int = 0

    def append(self, leaf: _Leaf, *, preformatted: bool = False, track_bracket: bool = False) -> None:
        if self.inside_brackets or not preformatted or track_bracket:
            self.bracket_tracker.mark(leaf)
            if self.has_magic_trailing_comma(leaf):
                self.magic_trailing_comma = leaf
        self.leaves.append(leaf)

    @property
    def is_def(self) -> bool:
        return bool(self.leaves) and (
            _is_name(self.leaves[0], "def")
            or (_is_name(self.leaves[0], "async") and len(self.leaves) > 1 and _is_name(self.leaves[1], "def"))
        )

    @property
    def is_chained_assignment(self) -> bool:
        return sum(leaf.type == token.EQUAL for leaf in self.leaves) > 1

    def has_magic_trailing_comma(self, closing: _Leaf) -> bool:
        if not (closing.type in CLOSING_BRACKETS and self.leaves and self.leaves[-1].type == token.COMMA):
            return False
        if closing.type == token.RBRACE:
            return True
        opening = closing.opening_bracket
        if closing.type == token.RSQB:
            return not (
                opening is not None
                and opening.kind is _Kind.TRAILER
                and _is_one_sequence_between(opening, closing, self.leaves, (token.LSQB, token.RSQB))
            )
        return opening is not None and not _is_one_sequence_between(opening, closing, self.leaves)

    def enumerate_with_length(self, *, is_reversed: bool = False) -> Iterator[tuple[int, _Leaf, int]]:
        indexed = reversed(list(enumerate(self.leaves))) if is_reversed else enumerate(self.leaves)
        for index, leaf in indexed:
            yield index, leaf, len(leaf.prefix) + len(leaf.value)

    def clone(self) -> _Line:
        return _Line(
            depth=self.depth,
            inside_brackets=self.inside_brackets,
            should_split_rhs=self.should_split_rhs,
            magic_trailing_comma=self.magic_trailing_comma,
        )

    def __str__(self) -> str:
        first, *rest = self.leaves
        return f"{first.prefix}{'    ' * self.depth}{first.value}{''.join(map(str, rest))}"

    def __bool__(self) -> bool:
        return bool(self.leaves)


@dataclass
class _RHSResult:
    head: _Line
    body: _Line
    tail: _Line
    opening_bracket: _Leaf
    closing_bracket: _Leaf


def _is_one_sequence_between(
    opening: _Leaf,
    closing: _Leaf,
    leaves: list[_Leaf],
    brackets: tuple[int, int] = (token.LPAR, token.RPAR),
) -> bool:
    if (opening.type, closing.type) != brackets:
        return False
    depth = closing.bracket_depth + 1
    opening_index = next(index for index, leaf in enumerate(leaves) if leaf is opening)
    commas = 0
    for leaf in leaves[opening_index + 1 :]:
        if leaf is closing:
            break
        if leaf.bracket_depth == depth and leaf.type == token.COMMA:
            commas += 1
            if leaf.arglist:
                commas += 1
                break
    return commas < 2  # noqa: PLR2004


@dataclass(frozen=True)
class _Mode:
    line_length: int
    wrap_string_literal: bool
    restore: Callable[[str], str] = str


def _is_line_short_enough(line: _Line, mode: _Mode, line_str: str = "") -> bool:
    return str_width(mode.restore(line_str or str(line))) <= mode.line_length


def _get_leaves_inside_matching_brackets(leaves: Sequence[_Leaf]) -> set[int]:
    start_index = next((index for index, leaf in enumerate(leaves) if leaf.type in OPENING_BRACKETS), None)
    if start_index is None:
        return set()
    bracket_stack: list[tuple[int, int]] = []
    ids: set[int] = set()
    for index in range(start_index, len(leaves)):
        leaf = leaves[index]
        if leaf.type in OPENING_BRACKETS:
            bracket_stack.append((BRACKET[leaf.type], index))
        if leaf.type in CLOSING_BRACKETS:
            if not bracket_stack or leaf.type != bracket_stack[-1][0]:
                break
            _, start = bracket_stack.pop()
            ids.update(id(leaves[inner]) for inner in range(start, index + 1))
    return ids


def _should_split_line(line: _Line, opening_bracket: _Leaf) -> bool:
    exclude: set[int] = set()
    trailing_comma = False
    if (last_leaf := line.leaves[-1]).type == token.COMMA:
        trailing_comma = True
        exclude.add(id(last_leaf))
    try:
        max_priority = line.bracket_tracker.max_delimiter_priority(exclude=exclude)
    except ValueError:
        return False
    return max_priority == COMMA_PRIORITY and (trailing_comma or opening_bracket.kind is _Kind.ATOM)


def _ensure_trailing_comma(leaves: list[_Leaf], original: _Line, opening_bracket: _Leaf) -> bool:
    """Return whether the body of a split adds a trailing comma: standalone arguments of a definition.

    A return annotation, whose body comes after the ``->``, takes none.
    """
    if not (
        original.is_def
        and opening_bracket.value == "("
        and leaves
        and leaves[-1].type != token.COMMA
        and not any(leaf.type == token.COMMA and not leaf.annotation for leaf in leaves)
    ):
        return False
    first = leaves[0]
    return all(item.type != token.RARROW for item in takewhile(lambda item: item is not first, original.leaves))


def _bracket_split_build_line(
    leaves: list[_Leaf], original: _Line, opening_bracket: _Leaf, *, component: _Component
) -> _Line:
    result = _Line(depth=original.depth)
    if component is _Component.BODY:
        result.inside_brackets = True
        result.depth += 1
        if _ensure_trailing_comma(leaves, original, opening_bracket):
            leaves.append(_comma())
    leaves_to_track = _get_leaves_inside_matching_brackets(leaves) if component is _Component.HEAD else set()
    for leaf in leaves:
        result.append(leaf, preformatted=True, track_bracket=id(leaf) in leaves_to_track)
    if component is _Component.BODY and _should_split_line(result, opening_bracket):
        result.should_split_rhs = True
    return result


def _first_right_hand_split(line: _Line, omit: Collection[int] = ()) -> _RHSResult:
    tail_leaves: list[_Leaf] = []
    body_leaves: list[_Leaf] = []
    head_leaves: list[_Leaf] = []
    current_leaves = tail_leaves
    opening_bracket: _Leaf | None = None
    closing_bracket: _Leaf | None = None
    for leaf in reversed(line.leaves):
        if current_leaves is body_leaves and leaf is opening_bracket:
            current_leaves = head_leaves if body_leaves else tail_leaves
        current_leaves.append(leaf)
        if current_leaves is tail_leaves and leaf.type in CLOSING_BRACKETS and id(leaf) not in omit:
            opening_bracket = leaf.opening_bracket
            closing_bracket = leaf
            current_leaves = body_leaves
    if not (opening_bracket and closing_bracket and head_leaves):
        msg = "No brackets found"
        raise CannotSplitError(msg)
    tail_leaves.reverse()
    body_leaves.reverse()
    head_leaves.reverse()
    head = _bracket_split_build_line(head_leaves, line, opening_bracket, component=_Component.HEAD)
    body = _bracket_split_build_line(body_leaves, line, opening_bracket, component=_Component.BODY)
    tail = _bracket_split_build_line(tail_leaves, line, opening_bracket, component=_Component.TAIL)
    return _RHSResult(head, body, tail, opening_bracket, closing_bracket)


def _can_be_split(line: _Line) -> bool:
    """Return False when the line cannot be split for sure: one leaf, or the attributes and calls of a string.

    Black walks such a chain back from its end and gives False on every walk, at the latest at the string.
    """
    leaves = line.leaves
    return len(leaves) >= 2 and not (leaves[0].type == token.STRING and leaves[1].type == token.DOT)  # noqa: PLR2004


def _can_omit_opening_paren(line: _Line, *, first: _Leaf, line_length: int) -> bool:
    remainder = False
    length = 4 * line.depth
    for _, leaf, leaf_length in line.enumerate_with_length():
        if leaf.type in CLOSING_BRACKETS and leaf.opening_bracket is first:
            remainder = True
        if remainder:
            length += leaf_length
            if length > line_length:
                return False
            if leaf.type in OPENING_BRACKETS:
                remainder = False
    return True


def _can_omit_closing_paren(line: _Line, *, last: _Leaf, line_length: int) -> bool:
    length = 4 * line.depth
    seen_other_brackets = False
    for _, leaf, leaf_length in line.enumerate_with_length():
        length += leaf_length
        if leaf is last.opening_bracket:
            if seen_other_brackets or length <= line_length:
                return True
        elif leaf.type in OPENING_BRACKETS:
            seen_other_brackets = True
    return False


def _can_omit_invisible_parens(rhs: _RHSResult, line_length: int) -> bool:  # noqa: PLR0911
    line = rhs.body
    bracket_tracker = line.bracket_tracker
    if not bracket_tracker.delimiters:
        return True
    max_priority = bracket_tracker.max_delimiter_priority()
    if bracket_tracker.delimiter_count_with_priority(max_priority) > 1:
        return False
    if max_priority == DOT_PRIORITY:
        return True
    first, second = line.leaves[:2]
    if (
        first.type in OPENING_BRACKETS
        and second.type not in CLOSING_BRACKETS
        and _can_omit_opening_paren(line, first=first, line_length=line_length)
    ):
        return True
    penultimate, last = line.leaves[-2:]
    if last.type in {token.RPAR, token.RBRACE} or (
        last.type == token.RSQB and last.opening_bracket is not None and last.opening_bracket.kind is not _Kind.TRAILER
    ):
        if penultimate.type in OPENING_BRACKETS:
            return False
        if _can_omit_closing_paren(line, last=last, line_length=line_length):
            return True
    return False


def _prefer_split_rhs_oop_over_rhs(rhs_oop: _RHSResult, rhs: _RHSResult, mode: _Mode) -> bool:  # noqa: PLR0911
    if not (len(rhs.head.leaves) >= 2 and rhs.head.leaves[-2].type == token.EQUAL):  # noqa: PLR2004
        return True
    if not any(leaf.type in BRACKETS for leaf in rhs.head.leaves[:-1]):
        return True
    if not _is_line_short_enough(rhs.head, _Mode(mode.line_length - 1, mode.wrap_string_literal, mode.restore)):
        return True
    if rhs.head.magic_trailing_comma is not None:
        return True
    rhs_head_equal_count = sum(leaf.type == token.EQUAL for leaf in rhs.head.leaves)
    rhs_oop_head_equal_count = sum(leaf.type == token.EQUAL for leaf in rhs_oop.head.leaves)
    if rhs_head_equal_count > 1 and rhs_head_equal_count > rhs_oop_head_equal_count:
        return False
    for leaf in reversed(rhs_oop.head.leaves):
        if leaf.type == token.EQUAL:
            break
        if leaf.type in CLOSING_BRACKETS:
            return True
    return any(leaf.type == token.EQUAL for leaf in rhs_oop.head.leaves) and _is_line_short_enough(rhs_oop.head, mode)


def _ensure_visible(leaf: _Leaf) -> None:
    if leaf.type == token.LPAR:
        leaf.value = "("
    elif leaf.type == token.RPAR:
        leaf.value = ")"


def _maybe_split_omitting_optional_parens(
    rhs: _RHSResult, line: _Line, mode: _Mode, features: Collection[_Feature], omit: Collection[int] = ()
) -> Iterator[_Line]:
    if (
        _Feature.FORCE_OPTIONAL_PARENTHESES not in features  # noqa: PLR0916
        and rhs.opening_bracket.type == token.LPAR
        and not rhs.opening_bracket.value
        and rhs.closing_bracket.type == token.RPAR
        and not rhs.closing_bracket.value
        and _can_omit_invisible_parens(rhs, mode.line_length)
    ):
        omit = {id(rhs.closing_bracket), *omit}
        try:
            rhs_oop = _first_right_hand_split(line, omit=omit)
            if _prefer_split_rhs_oop_over_rhs(rhs_oop, rhs, mode):
                yield from _maybe_split_omitting_optional_parens(rhs_oop, line, mode, features, omit)
                return
        except CannotSplitError as error:
            if (
                not line.is_chained_assignment
                and not _can_be_split(rhs.body)
                and not _is_line_short_enough(rhs.body, mode)
            ):
                msg = "Splitting failed, body is still too long and can't be split."
                raise CannotSplitError(msg) from error
    _ensure_visible(rhs.opening_bracket)
    _ensure_visible(rhs.closing_bracket)
    yield from (rhs.head, rhs.body, rhs.tail)


def _right_hand_split(
    line: _Line, mode: _Mode, features: Collection[_Feature], omit: Collection[int] = ()
) -> Iterator[_Line]:
    rhs = _first_right_hand_split(line, omit=omit)
    yield from _maybe_split_omitting_optional_parens(rhs, line, mode, features, omit)


def _generate_trailers_to_omit(line: _Line, line_length: int) -> Iterator[set[int]]:
    omit: set[int] = set()
    if not line.magic_trailing_comma:
        yield omit
    length = 4 * line.depth
    opening_bracket: _Leaf | None = None
    closing_bracket: _Leaf | None = None
    inner_brackets: set[int] = set()
    for index, leaf, leaf_length in line.enumerate_with_length(is_reversed=True):
        length += leaf_length
        if length > line_length:
            break
        previous = line.leaves[index - 1] if index > 0 else None
        if opening_bracket:
            if leaf is opening_bracket:
                opening_bracket = None
            elif leaf.type in CLOSING_BRACKETS:
                if _has_trailing_comma_before(previous, leaf, line):
                    break
                inner_brackets.add(id(leaf))
        elif leaf.type in CLOSING_BRACKETS:
            if previous is not None and previous.type in OPENING_BRACKETS:
                inner_brackets.add(id(leaf))
                continue
            if closing_bracket:
                omit.add(id(closing_bracket))
                omit.update(inner_brackets)
                inner_brackets.clear()
                yield omit
            if _has_trailing_comma_before(previous, leaf, line):
                break
            if leaf.value:
                opening_bracket = leaf.opening_bracket
                closing_bracket = leaf


def _has_trailing_comma_before(previous: _Leaf | None, closing: _Leaf, line: _Line) -> bool:
    return (
        previous is not None
        and previous.type == token.COMMA
        and closing.opening_bracket is not None
        and not _is_one_sequence_between(closing.opening_bracket, closing, line.leaves)
    )


def _left_hand_split(line: _Line, _mode: _Mode, _features: Collection[_Feature]) -> Iterator[_Line]:
    """Split a definition at its first bracket pair, as black splits a ``def`` line."""
    for leaf_type in (token.LPAR, token.LSQB):
        tail_leaves: list[_Leaf] = []
        body_leaves: list[_Leaf] = []
        head_leaves: list[_Leaf] = []
        current_leaves = head_leaves
        matching_bracket: _Leaf | None = None
        for leaf in line.leaves:
            if (
                current_leaves is body_leaves
                and leaf.type in CLOSING_BRACKETS
                and matching_bracket is not None
                and leaf.opening_bracket is matching_bracket
            ):
                _ensure_visible(leaf)
                _ensure_visible(matching_bracket)
                current_leaves = tail_leaves if body_leaves else head_leaves
            current_leaves.append(leaf)
            if current_leaves is head_leaves and leaf.type == leaf_type:
                matching_bracket = leaf
                current_leaves = body_leaves
        if matching_bracket and tail_leaves:
            break
    if not matching_bracket or not tail_leaves:
        msg = "No brackets found"
        raise CannotSplitError(msg)
    head = _bracket_split_build_line(head_leaves, line, matching_bracket, component=_Component.HEAD)
    body = _bracket_split_build_line(body_leaves, line, matching_bracket, component=_Component.BODY)
    tail = _bracket_split_build_line(tail_leaves, line, matching_bracket, component=_Component.TAIL)
    yield from (head, body, tail)


def _should_split_funcdef_with_rhs(line: _Line) -> bool:
    """Return whether the return annotation of a definition has a magic trailing comma."""
    return_type_leaves: list[_Leaf] = []
    in_return_type = False
    for leaf in line.leaves:
        if leaf.type == token.COLON:
            in_return_type = False
        if in_return_type:
            return_type_leaves.append(leaf)
        if leaf.type == token.RARROW:
            in_return_type = True
    result = _Line(depth=line.depth)
    leaves_to_track = _get_leaves_inside_matching_brackets(return_type_leaves)
    for leaf in return_type_leaves:
        result.append(leaf, preformatted=True, track_bracket=id(leaf) in leaves_to_track)
    return result.magic_trailing_comma is not None


def _rhs(line: _Line, mode: _Mode, features: Collection[_Feature]) -> Iterator[_Line]:
    for omit in _generate_trailers_to_omit(line, mode.line_length):
        lines = list(_right_hand_split(line, mode, features, omit=omit))
        if _is_line_short_enough(lines[0], mode):
            yield from lines
            return
    yield from _right_hand_split(line, mode, features)


def _delimiter_split(line: _Line, _mode: _Mode, _features: Collection[_Feature]) -> Iterator[_Line]:
    last_leaf = line.leaves[-1]
    bracket_tracker = line.bracket_tracker
    try:
        delimiter_priority = bracket_tracker.max_delimiter_priority(exclude={id(last_leaf)})
    except ValueError:
        msg = "No delimiters found"
        raise CannotSplitError(msg) from None
    if delimiter_priority == DOT_PRIORITY and bracket_tracker.delimiter_count_with_priority(delimiter_priority) == 1:
        msg = "Splitting a single attribute from its owner looks wrong"
        raise CannotSplitError(msg)
    new_line = partial(_Line, depth=line.depth, inside_brackets=line.inside_brackets, split_priority=delimiter_priority)
    current_line = new_line()
    for leaf in line.leaves:
        if not current_line.leaves:
            leaf.prefix = ""
        current_line.append(leaf, preformatted=True)
        if bracket_tracker.delimiters.get(id(leaf)) == delimiter_priority:
            yield current_line
            current_line = new_line()
    if current_line:
        if delimiter_priority == COMMA_PRIORITY and current_line.leaves[-1].type != token.COMMA:
            current_line.append(_comma())
        yield current_line


def _wrapped_string_lines(line: _Line, mode: _Mode, _features: Collection[_Feature]) -> Iterator[_Line]:
    """Wrap a too long ``name='text'`` argument into parenthesized implicitly joined strings.

    Implicitly joined strings are wrapped as one. A bytes literal or an f-string stays whole, and so do a parameter's
    default and a piece of a split at another delimiter than a comma, which is one operand of an expression.
    """
    leaves = line.leaves
    trailing_comma = leaves[-1].type == token.COMMA
    strings = leaves[2 : len(leaves) - trailing_comma]
    if not (
        line.split_priority in {0, COMMA_PRIORITY}
        and len(leaves) - trailing_comma > 2  # noqa: PLR2004
        and leaves[0].type == token.NAME
        and leaves[1].type == token.EQUAL
        and not leaves[1].parameter_default
        and all(leaf.type == token.STRING and leaf.value.lstrip("rRuU")[0] not in "bBfF" for leaf in strings)
        and str_width(mode.restore(str(line).removesuffix(","))) > mode.line_length
    ):
        msg = "Not a long keyword string argument"
        raise CannotSplitError(msg)
    value_repr = string_repr(mode.restore(ast.literal_eval(" ".join(leaf.value for leaf in strings))), NEWEST_TARGET)
    quote = value_repr[0]
    escaped = value_repr[1:-1] if quote in {"'", '"'} and value_repr.endswith(quote) else value_repr
    head = _Line(depth=line.depth)
    for leaf in (*leaves[:2], _Leaf(token.LPAR, "(")):
        head.leaves.append(leaf)
    yield head
    max_content_length = mode.line_length - 4 * (line.depth + 1) - 2
    for chunk in split_escaped_string_literal(escaped, max_content_length):
        chunk_line = _Line(depth=line.depth + 1)
        chunk_line.leaves.append(_Leaf(token.STRING, f"{quote}{chunk}{quote}"))
        yield chunk_line
    tail = _Line(depth=line.depth)
    tail.leaves.append(_Leaf(token.RPAR, ")"))
    if trailing_comma:
        tail.leaves.append(leaves[-1])
    yield tail


def _transform_line(line: _Line, mode: _Mode, features: Collection[_Feature] = ()) -> Iterator[_Line]:
    line_str = str(line)
    if not line.should_split_rhs and not line.magic_trailing_comma and _is_line_short_enough(line, mode, line_str):
        yield line
        return
    transforms: list[Callable[[_Line, _Mode, Collection[_Feature]], Iterator[_Line]]]
    if line.inside_brackets:
        transforms = (
            [_wrapped_string_lines, _delimiter_split, _rhs]
            if mode.wrap_string_literal
            else [
                _delimiter_split,
                _rhs,
            ]
        )
    elif line.is_def and not _should_split_funcdef_with_rhs(line):
        transforms = [_left_hand_split]
    else:
        transforms = [_rhs]
    for transform in transforms:
        try:
            result = _run_transformer(line, transform, mode, features)
        except CannotSplitError:
            continue
        yield from result
        return
    yield line


def _run_transformer(
    line: _Line,
    transform: Callable[[_Line, _Mode, Collection[_Feature]], Iterator[_Line]],
    mode: _Mode,
    features: Collection[_Feature],
) -> list[_Line]:
    result = [
        result_line
        for transformed_line in transform(line, mode, features)
        for result_line in _transform_line(transformed_line, mode, features)
    ]
    if (
        _Feature.FORCE_OPTIONAL_PARENTHESES in features  # noqa: PLR0916
        or transform is not _rhs
        or not line.bracket_tracker.invisible
        or any(bracket.value for bracket in line.bracket_tracker.invisible)
        or _is_line_short_enough(result[0], mode)
        or any(leaf.synthetic for leaf in line.leaves)
    ):
        return result
    line_copy = line.clone()
    for index, leaf in enumerate(line.leaves):
        copied = leaf.copy()
        copied.prefix = copied.whitespace if index else ""
        line_copy.append(copied)
    second_opinion = _run_transformer(line_copy, transform, mode, {*features, _Feature.FORCE_OPTIONAL_PARENTHESES})
    if all(_is_line_short_enough(second_line, mode) for second_line in second_opinion):
        return second_opinion
    return result


_ESCAPE_UNIT = re.compile(r"\\(?:x[0-9a-fA-F]{2}|u[0-9a-fA-F]{4}|U[0-9a-fA-F]{8}|.)|.", re.DOTALL)
_LONGEST_ESCAPE_UNIT = 10


def _escape_boundary(text: str, limit: int) -> int:
    """Return the end of the last whole character or escape sequence of ``text`` within ``limit``.

    The first one is returned when it is longer than ``limit``, so that a split always makes progress.
    """
    ends = [unit.end() for unit in _ESCAPE_UNIT.finditer(text[: limit + _LONGEST_ESCAPE_UNIT])]
    return max((end for end in ends if end <= limit), default=ends[0])


def split_escaped_string_literal(content: str, max_length: int) -> list[str]:
    """Split the escaped content of a string literal at spaces, else between characters and escape sequences.

    A literal is left whole when ``max_length`` leaves no room for any content.
    """
    if max_length < 1:
        return [content]
    chunks: list[str] = []
    remaining = content
    while len(remaining) > max_length:
        split_index = remaining.rfind(" ", 0, max_length + 1)
        if split_index <= 0:
            split_index = _escape_boundary(remaining, max_length)
        chunks.append(remaining[:split_index])
        remaining = remaining[split_index:]
    if remaining or not chunks:
        chunks.append(remaining)
    return chunks


class _Unsupported(Exception):  # noqa: N818
    """The line holds a construct the port does not lay out."""


def _operand_end(leaf: _Leaf | None) -> bool:
    """Return whether ``leaf`` ends an operand, so that a following sign or star is a binary operator."""
    if leaf is None:
        return False
    if leaf.type == token.NAME:
        return leaf.value in _VALUE_KEYWORDS or not keyword.iskeyword(leaf.value)
    return leaf.type in {token.NUMBER, token.STRING, token.ELLIPSIS} or leaf.type in CLOSING_BRACKETS


def _read_leaves(tokens: Iterable[tokenize.TokenInfo]) -> tuple[list[_Leaf], int]:  # noqa: PLR0912, PLR0915
    """Turn the tokens of one logical line into leaves with the facts black reads from its tree; give its last row."""
    leaves: list[_Leaf] = []
    stack: list[tuple[_Leaf, list[str]]] = []
    previous_row = 0
    for item in tokens:
        if item.type in {tokenize.NL, tokenize.INDENT, tokenize.DEDENT}:
            continue
        if item.type in {tokenize.COMMENT, tokenize.ERRORTOKEN} or (
            item.type == tokenize.STRING and "\n" in item.string
        ):
            raise _Unsupported
        if leaves and item.start[0] != previous_row and not stack:
            raise _Unsupported
        previous_row = item.end[0]
        if item.type not in {tokenize.NAME, tokenize.NUMBER, tokenize.STRING, tokenize.OP}:
            raise _Unsupported
        type_ = item.exact_type
        leaf = _Leaf(type_, item.string)
        previous = leaves[-1] if leaves else None
        enclosing = stack[-1] if stack else None
        if type_ in OPENING_BRACKETS:
            if type_ != token.LBRACE and previous is not None and _operand_end(previous):
                before = leaves[-2] if len(leaves) > 1 else None
                leaf.kind = _Kind.TRAILER
                if type_ == token.LPAR and before is not None and _is_name(before, "class"):
                    leaf.kind = _Kind.CLASSDEF
                elif type_ == token.LPAR and before is not None and _is_name(before, "def"):
                    leaf.kind = _Kind.PARAMETERS
            stack.append((leaf, []))
        elif type_ in CLOSING_BRACKETS:
            if not stack:
                raise _Unsupported
            stack.pop()
        elif type_ == token.COMMA:
            leaf.arglist = (
                enclosing is not None and enclosing[0].type == token.LPAR and enclosing[0].kind is not _Kind.ATOM
            )
        elif type_ in _UNARY_CANDIDATES:
            leaf.unary = not _operand_end(previous)
            if type_ == token.DOUBLESTAR and not leaf.unary:
                raise _Unsupported
        elif type_ == token.DOT:
            if previous is not None and previous.type == token.NUMBER and previous.value.replace("_", "").isdigit():
                raise _Unsupported
        elif type_ == token.COLON:
            if enclosing is not None and enclosing[0].type == token.LSQB and enclosing[0].kind is _Kind.TRAILER:
                raise _Unsupported
        elif type_ == token.NAME:
            seen = enclosing[1] if enclosing is not None else []
            match item.string:
                case "for" if previous is not None and not _is_name(previous, "async"):
                    leaf.priority_name = COMPREHENSION_PRIORITY
                    seen.append("for")
                case "if" if seen and "for" in seen:
                    leaf.priority_name = COMPREHENSION_PRIORITY
                case "if" | "else":
                    leaf.priority_name = TERNARY_PRIORITY
                case "in":
                    leaf.comparator = not (seen and seen[-1:] == ["for"]) and not (
                        previous is not None and _is_name(previous, "not")
                    )
                    if seen and seen[-1:] == ["for"]:
                        seen.append("in")
                case "not":
                    leaf.comparator = previous is None or not _is_name(previous, "is")
                    leaf.unary = True
                case "is":
                    leaf.comparator = True
                case "and" | "or":
                    leaf.logic = True
        leaves.append(leaf)
    for index, leaf in enumerate(leaves):
        if _is_name(leaf, "not"):
            following = leaves[index + 1] if index + 1 < len(leaves) else None
            leaf.comparator = leaf.comparator and following is not None and _is_name(following, "in")
            leaf.unary = not leaf.comparator
        if _is_name(leaf, "lambda") and index + 1 < len(leaves) and leaves[index + 1].type != token.COLON:
            raise _Unsupported
    return leaves, previous_row


def _set_whitespace(leaves: list[_Leaf]) -> None:
    """Give each leaf the whitespace black puts before it.

    In a definition's parameters a default takes spaces around its ``=`` when its parameter is annotated, and the
    commas inside an annotation are marked, as black reads both from its tree.
    """
    stack: list[_Leaf] = []
    annotated = False
    previous: _Leaf | None = None
    for leaf in leaves:
        enclosing = stack[-1] if stack else None
        in_parameters = enclosing is not None and enclosing.kind is _Kind.PARAMETERS
        if in_parameters and leaf.type == token.COMMA:
            annotated = False
        leaf.whitespace = _whitespace(leaf, previous, enclosing)
        if in_parameters and annotated and leaf.type == token.EQUAL:
            leaf.whitespace = " "
        elif in_parameters and previous is not None and previous.type == token.EQUAL:
            leaf.whitespace = previous.whitespace
        if in_parameters and leaf.type == token.COLON:
            annotated = True
        if in_parameters and leaf.type == token.EQUAL:
            annotated = False
            leaf.parameter_default = True
        if leaf.type == token.COMMA and annotated and any(item.kind is _Kind.PARAMETERS for item in stack[:-1]):
            leaf.annotation = True
        leaf.prefix = leaf.whitespace
        if leaf.type in OPENING_BRACKETS:
            stack.append(leaf)
        elif leaf.type in CLOSING_BRACKETS:
            stack.pop()
        previous = leaf


def _whitespace(leaf: _Leaf, previous: _Leaf | None, enclosing: _Leaf | None) -> str:  # noqa: PLR0911
    keyword_argument = enclosing is not None and enclosing.type == token.LPAR and enclosing.kind is not _Kind.ATOM
    if previous is None or leaf.type in CLOSING_BRACKETS or leaf.type in {token.COMMA, token.COLON}:
        return ""
    if previous.type in OPENING_BRACKETS or token.DOT in {previous.type, leaf.type}:
        return ""
    if leaf.type in OPENING_BRACKETS and leaf.kind is not _Kind.ATOM:
        return ""
    if leaf.type == token.EQUAL and keyword_argument:
        return ""
    if previous.type == token.EQUAL and keyword_argument:
        return ""
    if previous.unary and previous.type != token.NAME:
        return ""
    return " "


def _statement_children(leaves: list[_Leaf]) -> list[tuple[int, int]]:
    """Return the spans black wraps in invisible parentheses: after an annotation colon and after each ``=``.

    An annotation that holds a lambda before the value's ``=`` is not laid out.
    """
    depth = 0
    boundaries: list[int] = []
    annotation_seen = False
    lambda_seen = False
    for index, leaf in enumerate(leaves):
        if leaf.type in OPENING_BRACKETS:
            depth += 1
        elif leaf.type in CLOSING_BRACKETS:
            depth -= 1
        elif depth == 0:
            if leaf.type == token.EQUAL or leaf.type in _AUGMENTED_ASSIGNMENTS:
                if lambda_seen:
                    raise _Unsupported
                boundaries.append(index)
            elif leaf.type == token.COLON and not boundaries and not annotation_seen:
                annotation_seen = True
                boundaries.append(index)
            elif _is_name(leaf, "lambda"):
                lambda_seen = True
    ends = [*boundaries[1:], len(leaves)]
    return [(boundary + 1, end) for boundary, end in zip(boundaries, ends, strict=True)]


def _is_parenthesized_atom(leaves: list[_Leaf], start: int, end: int) -> bool:
    """Return whether the balanced ``leaves[start:end]`` is an atom in parentheses that close at its end."""
    if not (leaves[start].type == token.LPAR and leaves[start].kind is _Kind.ATOM):
        return False
    depth = 0
    for leaf in leaves[start : end - 1]:
        if leaf.type in OPENING_BRACKETS:
            depth += 1
        elif leaf.type in CLOSING_BRACKETS:
            depth -= 1
            if not depth:
                return False
    return True


def _definition_annotation_spans(leaves: list[_Leaf]) -> list[tuple[int, int]]:
    """Return the spans black wraps in invisible parentheses in a ``def`` line.

    They are the return annotation and each parameter annotation that is a ``|`` union.
    """
    spans: list[tuple[int, int]] = []
    depth = 0
    annotation_start: int | None = None
    for index, leaf in enumerate(leaves):
        if (
            depth == 1
            and annotation_start is not None
            and (leaf.type in {token.COMMA, token.EQUAL} or leaf.type in CLOSING_BRACKETS)
        ):
            span = leaves[annotation_start:index]
            if _top_level_types(span) & {token.VBAR}:
                spans.append((annotation_start, index))
            annotation_start = None
        if leaf.type in OPENING_BRACKETS:
            depth += 1
        elif leaf.type in CLOSING_BRACKETS:
            depth -= 1
        elif depth == 1 and leaf.type == token.COLON:
            annotation_start = index + 1
        elif depth == 0 and leaf.type == token.RARROW and leaves[-1].type == token.COLON:
            spans.append((index + 1, len(leaves) - 1))
            break
    return spans


def _top_level_types(leaves: list[_Leaf]) -> set[int]:
    depth = 0
    types: set[int] = set()
    for leaf in leaves:
        if leaf.type in OPENING_BRACKETS:
            depth += 1
        elif leaf.type in CLOSING_BRACKETS:
            depth -= 1
        elif not depth:
            types.add(leaf.type)
    return types


def _wrap_spans(leaves: list[_Leaf], spans: list[tuple[int, int]]) -> list[_Leaf]:
    """Wrap each span in invisible parentheses, or make the parentheses of a span that is one atom invisible."""
    for start, end in reversed(spans):
        if _is_parenthesized_atom(leaves, start, end):
            _make_parens_invisible(leaves, start, end)
        else:
            leaves = _wrap(leaves, start, end)
    return leaves


def _make_parens_invisible(leaves: list[_Leaf], start: int, end: int) -> None:
    """Make the parentheses of the atom ``leaves[start:end]`` invisible.

    They stay when they hold a tuple, a walrus, a yield or nothing.
    """
    middle = leaves[start + 1 : end - 1]
    if not middle:
        return
    tracker = _BracketTracker()
    for leaf in middle:
        tracker.mark(leaf)
    if tracker.delimiters and tracker.max_delimiter_priority() >= COMMA_PRIORITY:
        return
    if any(leaf.type == token.COLONEQUAL and leaf.bracket_depth == 0 for leaf in middle) or _is_name(
        middle[0], "yield"
    ):
        return
    if _is_parenthesized_atom(middle, 0, len(middle)):
        raise _Unsupported
    leaves[start].value = ""
    leaves[end - 1].value = ""


def _wrap(leaves: list[_Leaf], start: int, end: int) -> list[_Leaf]:
    first = leaves[start]
    lpar = _Leaf(token.LPAR, "")
    lpar.whitespace = lpar.prefix = first.whitespace
    first.whitespace = first.prefix = ""
    rpar = _Leaf(token.RPAR, "")
    return [*leaves[:start], lpar, *leaves[start:end], rpar, *leaves[end:]]


def _joined_token(lines: list[str], span: tuple[tokenize.TokenInfo, tokenize.TokenInfo]) -> tokenize.TokenInfo:
    """Return the token of ``span``, an f-string from its start token to its end token as one STRING token."""
    first, last = span
    if first is last:
        return first
    (start_row, start_col), (end_row, end_col) = first.start, last.end
    if start_row != end_row:
        raise _Unsupported
    return tokenize.TokenInfo(tokenize.STRING, lines[start_row - 1][start_col:end_col], first.start, last.end, "")


def _source_tokens(source: str) -> Iterator[tokenize.TokenInfo]:
    """Return the tokens of the logical line that starts ``source``, each f-string as one STRING token."""
    tokens = map(partial(_joined_token, source.split("\n")), string_token_spans(source, template_strings=False))
    return takewhile(lambda item: item.type not in {tokenize.NEWLINE, tokenize.ENDMARKER}, tokens)


def format_logical_line(
    source: str,
    *,
    line_length: int,
    wrap_string_literal: bool,
    assignment: bool,
    restore: Callable[[str], str] = str,
) -> tuple[int, list[str]] | None:
    """Lay out the logical line that starts ``source`` (its first line indented) as black lays it out.

    Return the number of physical lines it took and its new lines. ``assignment`` puts black's invisible
    parentheses after an annotation colon and after each ``=`` (not in a ``type`` statement). ``None`` means the line
    holds a construct this port does not lay out. A changed line is laid out once more, as black formats a changed
    file a second time: a trailing comma the first pass added can make the second split the line elsewhere.
    """
    lay_out = partial(
        _lay_out_logical_line,
        line_length=line_length,
        wrap_string_literal=wrap_string_literal,
        assignment=assignment,
        restore=restore,
    )
    if (result := lay_out(source)) is None:
        return None
    rows, formatted = result
    if formatted != source.split("\n")[:rows]:
        formatted = (lay_out("\n".join(formatted)) or result)[1]
    return rows, formatted


def _lay_out_logical_line(
    source: str,
    *,
    line_length: int,
    wrap_string_literal: bool,
    assignment: bool,
    restore: Callable[[str], str],
) -> tuple[int, list[str]] | None:
    indent = len(source) - len(source.lstrip(" "))
    if indent % 4 or source[indent : indent + 1] == "\t":
        return None
    try:
        leaves, rows = _read_leaves(_source_tokens(source[indent:]))
        _set_whitespace(leaves)
        if _Line(leaves=leaves).is_def:
            if len(leaves) > 2 and leaves[2].type == token.LSQB:  # noqa: PLR2004
                return None
            leaves = _wrap_spans(leaves, _definition_annotation_spans(leaves))
        elif assignment and not (_is_name(leaves[0], "type") and len(leaves) > 1 and leaves[1].type == token.NAME):
            leaves = _wrap_spans(leaves, _statement_children(leaves))
    except (_Unsupported, tokenize.TokenError, SyntaxError):
        return None
    line = _Line(depth=indent // 4)
    for leaf in leaves:
        line.append(leaf)
    return rows, [str(result) for result in _transform_line(line, _Mode(line_length, wrap_string_literal, restore))]
