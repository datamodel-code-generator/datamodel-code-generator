"""Lay out one logical line of generated Python as black's line splitting lays it out.

A port of black's right-hand split, delimiter split and optional-parentheses rules for the statements dcg generates:
assignments, annotated assignments, expression, ``return``, ``del``, ``assert`` and ``raise`` statements, decorators
and the headers of definitions and compound statements. With ``wrap_string_literal`` it also ports the string
transformers of black's preview string processing (merge, paren strip, split, paren wrap) and the parentheses black's
``wrap_long_dict_values_in_parens`` puts around dictionary values. The tree facts black reads from its parse tree (what
a bracket opens, which operators are unary, which commas separate arguments, which leaves a string transformer took out
of the tree) are read from the tokens instead. A line the port does not cover (comments, multi-line strings, backslash
continuations, slices, power operators, lambdas with parameters) gives ``None`` and is left as it is.
"""

from __future__ import annotations

import keyword
import re
import string
import token
import tokenize
from bisect import bisect_right
from dataclasses import dataclass, field
from enum import Enum, auto
from functools import cache, lru_cache, partial
from itertools import accumulate, takewhile
from typing import TYPE_CHECKING, Final

from datamodel_code_generator._python_decorator import string_token_spans

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
_SKIPPED_TOKENS: Final = frozenset({tokenize.NL, tokenize.INDENT, tokenize.DEDENT})
_LEAF_TOKENS: Final = frozenset({tokenize.NAME, tokenize.NUMBER, tokenize.STRING, tokenize.OP})
_LINE_END_TOKENS: Final = frozenset({tokenize.NEWLINE, tokenize.ENDMARKER})
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
        "assign_like",
        "bracket_depth",
        "comparator",
        "dict_entry",
        "dict_value",
        "in_annotation",
        "kind",
        "logic",
        "loop_in",
        "opening_bracket",
        "orphaned",
        "parameter_default",
        "prefix",
        "priority_name",
        "synthetic",
        "type",
        "unary",
        "value",
        "whitespace",
        "wrap_element",
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
        self.loop_in = False
        self.priority_name = 0
        self.synthetic = False
        self.parameter_default = False
        self.assign_like = ""
        self.wrap_element = False
        self.dict_entry = False
        self.dict_value = False
        self.in_annotation = False
        self.orphaned = False
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
        leaf.loop_in = self.loop_in
        leaf.priority_name = self.priority_name
        leaf.synthetic = self.synthetic
        leaf.parameter_default = self.parameter_default
        leaf.assign_like = self.assign_like
        leaf.wrap_element = self.wrap_element
        leaf.dict_entry = self.dict_entry
        leaf.dict_value = self.dict_value
        leaf.in_annotation = self.in_annotation
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
        type_ = leaf.type
        if not self.depth and type_ in CLOSING_BRACKETS and (0, type_) not in self.bracket_match:
            return
        name = leaf.value if type_ == token.NAME else ""
        if self.for_loop_depths and self.for_loop_depths[-1] == self.depth and name == "in":
            self.depth -= 1
            self.for_loop_depths.pop()
        if self.lambda_argument_depths and self.lambda_argument_depths[-1] == self.depth and type_ == token.COLON:
            self.depth -= 1
            self.lambda_argument_depths.pop()
        if type_ in CLOSING_BRACKETS:
            self.depth -= 1
            leaf.opening_bracket = self.bracket_match.pop((self.depth, type_))
            if not leaf.value:
                self.invisible.append(leaf)
        leaf.bracket_depth = depth = self.depth
        if not depth:
            if (delimiter := _is_split_before_delimiter(leaf, self.previous)) and self.previous is not None:
                self.delimiters[id(self.previous)] = delimiter
            elif type_ == token.COMMA:
                self.delimiters[id(leaf)] = COMMA_PRIORITY
        if type_ in OPENING_BRACKETS:
            self.bracket_match[depth, BRACKET[type_]] = leaf
            self.depth += 1
            if not leaf.value:
                self.invisible.append(leaf)
        self.previous = leaf
        if name == "lambda":
            self.depth += 1
            self.lambda_argument_depths.append(self.depth)
        elif name == "for":
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
            if leaf.type in CLOSING_BRACKETS and self.has_magic_trailing_comma(leaf):
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
        return f"{first.prefix}{'    ' * self.depth}{first.value}{''.join([leaf.prefix + leaf.value for leaf in rest])}"

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
    """How a line is laid out; ``custom_splits`` holds where merged strings were joined, by the merged value."""

    line_length: int
    wrap_string_literal: bool
    restore: Callable[[str], str] = str
    custom_splits: dict[tuple[int, str], tuple[_CustomSplit, ...]] = field(default_factory=dict, compare=False)


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
        if result.inside_brackets or id(leaf) in leaves_to_track:
            result.append(leaf, preformatted=True, track_bracket=True)
        else:
            result.leaves.append(leaf)
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
    if (delimiter_count := bracket_tracker.delimiter_count_with_priority(max_priority)) > 1 or (
        delimiter_count == 1 and max_priority == COMMA_PRIORITY and _is_with_statement(rhs.head)
    ):
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


def _is_with_statement(line: _Line) -> bool:
    """Return whether ``line`` starts a ``with`` or ``async with`` statement."""
    leaves = line.leaves
    return bool(leaves) and (
        _is_name(leaves[0], "with")
        or (_is_name(leaves[0], "async") and len(leaves) > 1 and _is_name(leaves[1], "with"))
    )


def _prefer_split_rhs_oop_over_rhs(rhs_oop: _RHSResult, rhs: _RHSResult, mode: _Mode) -> bool:  # noqa: PLR0911
    if rhs.opening_bracket.dict_value and rhs.body.bracket_tracker.delimiters:
        return any(leaf.type == token.COLON for leaf in rhs_oop.tail.leaves)
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
                and not rhs.opening_bracket.dict_value
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


_STRING_OPERATORS: Final = frozenset({
    token.EQEQUAL,
    token.GREATER,
    token.GREATEREQUAL,
    token.LESS,
    token.LESSEQUAL,
    token.NOTEQUAL,
    token.PERCENT,
    token.PLUS,
    token.STAR,
})
_SPLIT_SAFE_CHARS: Final = frozenset(map(chr, (0x3001, 0x3002, 0xFF0C)))
_MIN_SUBSTR_SIZE: Final = 6
_STRING_PREFIX_CHARS: Final = "furbFURB"
_BREAK_MARK: Final = "@@@@@ BLACK BREAKPOINT MARKER @@@@@"
_EVEN_BACKSLASHES: Final = r"(?:(?<!\\)(?:\\\\)*)"
_OPEN_OCTAL_ESCAPE: Final = re.compile(rf"{_EVEN_BACKSLASHES}\\[0-7]{{1,2}}$")
_OCTAL_DIGITS: Final = frozenset(string.octdigits)
_DEBUG_EXPRESSION: Final = re.compile(r".*[\'\"].*(?<![!:=])={1}(?!=)(?![^\s:])")


@dataclass(frozen=True)
class _CustomSplit:
    """Where a merged string was joined: black re-splits it there when every piece fits."""

    has_prefix: bool
    break_idx: int


def _count_chars_in_width(text: str, max_width: int) -> int:
    """Return how many leading characters of ``text`` fit in ``max_width`` columns."""
    widths = accumulate(map(_char_width, text))
    return next((index for index, width in enumerate(widths) if width > max_width), len(text))


def _string_prefix(value: str) -> str:
    return value[: len(value) - len(value.lstrip(_STRING_PREFIX_CHARS))]


def _has_triple_quotes(value: str) -> bool:
    return value.lstrip(_STRING_PREFIX_CHARS)[:3] in {'"""', "'''"}


def _is_empty_par(leaf: _Leaf) -> bool:
    return leaf.type in {token.LPAR, token.RPAR} and not leaf.value


def iter_fexpr_spans(text: str) -> Iterator[tuple[int, int]]:
    """Yield the half-open spans of the replacement fields of an f-string (black's ``iter_fexpr_spans``)."""
    stack: list[int] = []
    index = 0
    while index < len(text):
        char = text[index]
        if char == "{":
            if not stack and text[index + 1 : index + 2] == "{":
                index += 2
                continue
            stack.append(index)
        elif char == "}":
            if stack and (start := stack.pop()) is not None and not stack:
                yield start, index + 1
        elif stack and (
            delimiter := text[index : index + 3] if text[index : index + 3] in {"'''", '"""'} else char
        ) in {
            "'''",
            '"""',
            "'",
            '"',
        }:
            index += len(delimiter)
            while index < len(text) and text[index : index + len(delimiter)] != delimiter:
                index += 1
            index += len(delimiter)
            continue
        index += 1


def _toggle_fexpr_quotes(text: str, old_quote: str) -> str:
    new_quote = "'" if old_quote == '"' else '"'
    parts: list[str] = []
    previous = 0
    for start, end in iter_fexpr_spans(text):
        parts.extend((text[previous:start], text[start:end].replace(old_quote, new_quote)))
        previous = end
    parts.append(text[previous:])
    return "".join(parts)


_TRAILER_START, _TRAILER_DOT, _TRAILER_NAME, _TRAILER_PERCENT, _TRAILER_ARGUMENT, _TRAILER_LPAR, _TRAILER_RPAR = range(
    7
)
_TRAILER_DONE: Final = -1
_TRAILER_ANY: Final = -1
_TRAILER_GOTO: Final = {
    (_TRAILER_START, token.DOT): _TRAILER_DOT,
    (_TRAILER_START, token.PERCENT): _TRAILER_PERCENT,
    (_TRAILER_START, _TRAILER_ANY): _TRAILER_DONE,
    (_TRAILER_DOT, token.NAME): _TRAILER_NAME,
    (_TRAILER_NAME, token.LPAR): _TRAILER_LPAR,
    (_TRAILER_NAME, _TRAILER_ANY): _TRAILER_DONE,
    (_TRAILER_PERCENT, token.LPAR): _TRAILER_LPAR,
    (_TRAILER_PERCENT, _TRAILER_ANY): _TRAILER_ARGUMENT,
    (_TRAILER_ARGUMENT, _TRAILER_ANY): _TRAILER_DONE,
    (_TRAILER_RPAR, _TRAILER_ANY): _TRAILER_DONE,
}


def _string_trailer_end(leaves: Sequence[_Leaf], string_idx: int) -> int:
    """Return the index after the trailer of the string at ``string_idx``: ``% args``, ``.name`` or ``.name(...)``.

    Black's ``StringParser``; invisible parentheses are skipped.
    """
    state = _TRAILER_START
    unmatched = 0
    index = string_idx + 1
    while index < len(leaves):
        leaf = leaves[index]
        if not _is_empty_par(leaf):
            unmatched += leaf.type == token.LPAR
            if state == _TRAILER_LPAR:
                if leaf.type == token.RPAR and not (unmatched := unmatched - 1):
                    state = _TRAILER_RPAR
            elif (state := _TRAILER_GOTO.get((state, leaf.type), _TRAILER_GOTO.get((state, _TRAILER_ANY)))) in {
                _TRAILER_DONE,
                None,
            }:
                break
        index += 1
    return index


def _append_copies(line: _Line, leaves: Iterable[_Leaf]) -> None:
    """Append a copy of each leaf with the whitespace black gives it in its new place (black's ``append_leaves``).

    The copy takes the leaf's place in black's tree, which leaves the leaf without a parent: ``orphaned``.
    """
    for leaf in leaves:
        copied = leaf.copy()
        copied.prefix = copied.whitespace if line.leaves else ""
        line.append(copied)
        leaf.orphaned = True


def _new_string(value: str, like: _Leaf) -> _Leaf:
    """Return a string leaf that takes the place of ``like``."""
    leaf = _Leaf(token.STRING, value)
    leaf.whitespace = like.whitespace
    leaf.in_annotation = like.in_annotation
    return leaf


def _string_merge(line: _Line, mode: _Mode, _features: Collection[_Feature]) -> Iterator[_Line]:
    """Merge each group of implicitly joined strings into one string and record where it was joined (StringMerger)."""
    leaves = line.leaves
    merged: dict[int, tuple[int, _Leaf]] = {}
    index = 0
    while index < len(leaves):
        end = index
        while end < len(leaves) and leaves[end].type == token.STRING:
            end += 1
        if (
            end - index > 1
            and not leaves[index].in_annotation
            and (group := _merged_string_group(leaves[index:end], mode))
        ):
            merged[index] = (end - index, group)
        index = max(end, index + 1)
    if not merged:
        msg = "No string group is merged"
        raise CannotSplitError(msg)
    new_line = line.clone()
    index = 0
    while index < len(leaves):
        if index in merged:
            count, string_leaf = merged[index]
            string_leaf.prefix = string_leaf.whitespace if new_line.leaves else ""
            new_line.append(string_leaf)
            index += count
            continue
        _append_copies(new_line, leaves[index : index + 1])
        index += 1
    yield new_line


def _has_other_quotes(value: str, prefix: str, quote: str) -> bool:
    """Return whether ``value`` is an f-string whose quotes would change meaning or validity when merged.

    That is an f-string with other quotes than ``quote`` and quotes inside, or one whose replacement field holds its
    own quote character (Python 3.12+), which toggling the quotes of the fields would turn into a delimiter.
    """
    if "f" not in prefix:
        return False
    body = value[len(prefix) + 1 : -1]
    if value[-1] != quote:
        return "'" in body or '"' in body
    return any(quote in value[start:end] for start, end in iter_fexpr_spans(value))


def _merged_string_group(group: Sequence[_Leaf], mode: _Mode) -> _Leaf | None:
    """Return the strings of ``group`` merged into one, or None when black does not merge them."""
    quote = group[0].value[-1]
    prefixes: set[str] = set()
    for leaf in group:
        prefix = _string_prefix(leaf.value).lower()
        if _has_triple_quotes(leaf.value) or "r" in prefix or _has_other_quotes(leaf.value, prefix, quote):
            return None
        prefixes.add(prefix)
    if len(prefixes) > 1 and prefixes != {"", "f"}:
        return None
    prefix = next((value for leaf in group if (value := _string_prefix(leaf.value).lower())), "")

    def make_naked(text: str, text_prefix: str) -> str:
        if "f" in text_prefix and not any(
            _DEBUG_EXPRESSION.search(text[start + 1 : end - 1]) for start, end in iter_fexpr_spans(text)
        ):
            text = _toggle_fexpr_quotes(text, quote)
        return re.sub(f"({_EVEN_BACKSLASHES}){quote}", rf"\1\\{quote}", text[len(text_prefix) + 1 : -1])

    merged = ""
    naked = ""
    has_prefixes: list[bool] = []
    for leaf in group:
        value = leaf.value
        next_prefix = _string_prefix(value).lower()
        if (
            _OPEN_OCTAL_ESCAPE.search(naked.replace(_BREAK_MARK, ""))
            and value[len(next_prefix) + 1 : len(next_prefix) + 2] in _OCTAL_DIGITS
        ):
            return None
        if "f" in prefix and "f" not in next_prefix:
            value = re.sub(r"(\{|\})", r"\1\1", value)
        has_prefixes.append(bool(next_prefix))
        merged = f"{prefix}{quote}{naked}{make_naked(value, next_prefix)}{_BREAK_MARK}{quote}"
        naked = make_naked(merged, prefix)
    custom_splits: list[_CustomSplit] = []
    rest = merged[len(prefix) + 1 : -1]
    for has_prefix in has_prefixes:
        mark = rest.find(_BREAK_MARK)
        rest = rest[mark + len(_BREAK_MARK) :]
        custom_splits.append(_CustomSplit(has_prefix, mark + (len(prefix) if has_prefix else 0) + 1))
    leaf = _new_string(merged.replace(_BREAK_MARK, ""), group[0])
    leaf.wrap_element = group[0].wrap_element
    leaf.dict_entry = group[0].dict_entry
    mode.custom_splits[id(leaf.value), leaf.value] = tuple(custom_splits)
    return leaf


_HIGHER_THAN_PERCENT_BEFORE: Final = frozenset({
    token.STAR,
    token.AT,
    token.SLASH,
    token.DOUBLESLASH,
    token.PERCENT,
    token.TILDE,
    token.DOUBLESTAR,
    token.LSQB,
    token.LPAR,
})
_HIGHER_THAN_PERCENT_AFTER: Final = frozenset({token.DOUBLESTAR, token.LSQB, token.LPAR, token.DOT})


def _string_paren_strip(line: _Line, _mode: _Mode, _features: Collection[_Feature]) -> Iterator[_Line]:
    """Remove parentheses around a string that do not call, index or hold a dictionary value (StringParenStripper)."""
    leaves = line.leaves
    strip: list[int] = []
    index = 0
    while index < len(leaves):
        leaf = leaves[index]
        if (
            leaf.type == token.STRING
            and index >= 1
            and (lpar := leaves[index - 1]).type == token.LPAR
            and lpar.value
            and not (
                (before := leaves[index - 2] if index >= 2 else None) is not None  # noqa: PLR2004
                and (before.type in {token.COLON, token.NAME} or before.type in CLOSING_BRACKETS)
            )
        ):
            end = _string_trailer_end(leaves, index)
            if not (
                before is not None
                and any(item.type == token.PERCENT for item in leaves[index - 1 : end])
                and (
                    before.type in _HIGHER_THAN_PERCENT_BEFORE
                    or (before.unary and before.type in {token.PLUS, token.MINUS})
                )
            ) and (
                end < len(leaves)
                and (rpar := leaves[end]).type == token.RPAR
                and rpar.value
                and not (end + 1 < len(leaves) and leaves[end + 1].type in _HIGHER_THAN_PERCENT_AFTER)
            ):
                strip.extend((index - 1, end))
        index += 1
    if not strip:
        msg = "This line has no strings wrapped in parens."
        raise CannotSplitError(msg)
    new_line = line.clone()
    removed = set(strip)
    for index, leaf in enumerate(leaves):
        leaf.orphaned = True
        if index in removed:
            continue
        if index - 1 in removed and leaves[index - 1].type == token.LPAR and leaf.type == token.STRING:
            string_leaf = _new_string(leaf.value, leaves[index - 1])
            string_leaf.wrap_element = leaves[index - 1].wrap_element
            string_leaf.prefix = string_leaf.whitespace if new_line.leaves else ""
            new_line.append(string_leaf)
            continue
        _append_copies(new_line, (leaf,))
    yield new_line


def _max_string_length(line: _Line, string_idx: int, mode: _Mode) -> int:
    """Return the length above which the string at ``string_idx`` is what makes the line too long."""
    leaves = line.leaves
    offset = line.depth * 4
    if string_idx >= 1:
        previous_idx = string_idx - 1
        if _is_empty_par(leaves[previous_idx]) and leaves[previous_idx].type == token.LPAR and string_idx >= 2:  # noqa: PLR2004
            previous_idx -= 1
        previous = leaves[previous_idx]
        if previous.type in _STRING_OPERATORS:
            offset += len(str(previous)) + 1
        else:
            offset += 1
            for leaf in reversed(leaves[: previous_idx + 1]):
                offset += len(str(leaf))
                if leaf.type in CLOSING_BRACKETS:
                    break
    if string_idx + 1 < len(leaves):
        following = leaves[string_idx + 1]
        if following.type == token.RPAR and not following.value and len(leaves) > string_idx + 2:
            following = leaves[string_idx + 2]
        offset += following.type == token.COMMA
        if string_idx + 2 < len(leaves) and following.type == token.DOT and leaves[string_idx + 2].type == token.NAME:
            offset += 1 + (string_idx + 3 < len(leaves) and leaves[string_idx + 3].type == token.LPAR)
            offset += len(leaves[string_idx + 2].value)
    return _count_chars_in_width(mode.restore(f"{line}\n"), mode.line_length - offset)


def _validate_split_string(line: _Line, string_idx: int, mode: _Mode) -> None:
    """Raise unless the string at ``string_idx`` makes the line too long and can be split (BaseStringSplitter)."""
    value = line.leaves[string_idx].value
    if len(value) <= _max_string_length(line, string_idx, mode) or _has_triple_quotes(value):
        msg = "The string itself is not what is causing this line to be too long."
        raise CannotSplitError(msg)


def _prefers_paren_wrap(leaves: Sequence[_Leaf]) -> bool:
    """Return whether the line starts with a string that is a whole element of a list, set or tuple."""
    return (
        leaves[0].type == token.STRING
        and leaves[0].wrap_element
        and not (len(leaves) > 1 and leaves[1].type == token.STRING)
    )


def _string_split(line: _Line, mode: _Mode, _features: Collection[_Feature]) -> Iterator[_Line]:  # noqa: PLR0912, PLR0914, PLR0915
    """Split a line that is one string, with an operator before it or a comma after it, at spaces (StringSplitter).

    Only inside brackets: black also splits a statement such as ``"text",``, whose pieces become separate statements.
    """
    leaves = line.leaves
    if not line.inside_brackets:
        msg = "The pieces of a string outside brackets would be separate statements."
        raise CannotSplitError(msg)
    if _prefers_paren_wrap(leaves):
        msg = "Line needs to be wrapped in parens first."
        raise CannotSplitError(msg)
    index = 0
    if len(leaves) > 1 and _is_name(leaves[0], "not") and _is_name(leaves[1], "in"):
        index = 2
    elif leaves[0].type in _STRING_OPERATORS or _is_name(leaves[0], "in"):
        index = 1
    if index >= len(leaves) or leaves[index].type != token.STRING:
        msg = "Line does not start with a string."
        raise CannotSplitError(msg)
    string_idx = index
    index = _string_trailer_end(leaves, string_idx)
    if index < len(leaves) and leaves[index].type == token.COMMA:
        index += 1
    if index < len(leaves):
        msg = "This line does not end with a string."
        raise CannotSplitError(msg)
    _validate_split_string(line, string_idx, mode)

    string_leaf = leaves[string_idx]
    string_leaf.orphaned = True
    quote = string_leaf.value[-1]
    prefix = _string_prefix(string_leaf.value).lower()
    drop_pointless_f_prefix = "f" in prefix and any(iter_fexpr_spans(string_leaf.value))
    operators = []
    while leaves[len(operators)].type in _STRING_OPERATORS or leaves[len(operators)].type == token.NAME:
        operators.append(leaves[len(operators)])
    operators_length = sum(len(str(leaf).strip()) for leaf in operators) + 1 if operators else 0
    ends_with_comma = string_idx + 1 < len(leaves) and leaves[string_idx + 1].type == token.COMMA
    max_last_string_column = mode.line_length - line.depth * 4 - ends_with_comma - operators_length
    if (max_break_width := mode.line_length - 1 - line.depth * 4) < 0:
        msg = "Unable to split at such high of a line depth."
        raise CannotSplitError(msg)
    custom_splits = list(mode.custom_splits.pop((id(string_leaf.value), string_leaf.value), ()))
    use_custom = bool(custom_splits) and all(split.break_idx <= max_break_width for split in custom_splits)
    rest = string_leaf.value
    first = True

    def piece_line(value: str) -> _Line:
        piece = line.clone()
        if first:
            _append_copies(piece, operators)
        piece_leaf = _new_string(value, string_leaf)
        piece_leaf.prefix = piece_leaf.whitespace if piece.leaves else ""
        piece.append(piece_leaf)
        return piece

    pieces: list[_Line] = []
    while len(custom_splits) > 1 if use_custom else str_width(mode.restore(rest)) > max_last_string_column:
        split: _CustomSplit | None = None
        if use_custom:
            split = custom_splits.pop(0)
            break_idx = split.break_idx
        else:
            max_break_idx = _count_chars_in_width(mode.restore(rest), max_break_width) - operators_length
            if (found := _break_index(rest, max_break_idx)) is None:
                if custom_splits:
                    rest, pieces, first, use_custom = string_leaf.value, [], True, True
                    continue
                break
            break_idx = found
        next_value = rest[:break_idx] + quote
        if (
            split is not None
            and not split.has_prefix
            and (next_value == prefix + quote or next_value != _normalized_f_string(next_value, prefix))
        ):
            break_idx += 1
            next_value = rest[:break_idx] + quote
        if drop_pointless_f_prefix:
            next_value = _normalized_f_string(next_value, prefix)
        pieces.append(piece_line(next_value))
        rest = prefix + quote + rest[break_idx:]
        first = False
    yield from pieces
    if drop_pointless_f_prefix:
        rest = _normalized_f_string(rest, prefix)
    last = piece_line(rest)
    trailing = leaves[string_idx + 1 :]
    if not trailing:
        yield last
        return
    following = rest
    for leaf in trailing:
        following += str(leaf)
        if leaf.type == token.LPAR:
            break
    if str_width(mode.restore(following)) <= max_last_string_column or trailing[0].type == token.COMMA:
        _append_copies(last, trailing)
        yield last
        return
    yield last
    rest_line = line.clone()
    _append_copies(rest_line, trailing)
    yield rest_line


def _normalized_f_string(value: str, prefix: str) -> str:
    """Drop the ``f`` prefix of a piece without replacement fields and undouble its braces."""
    if "f" not in prefix or any(iter_fexpr_spans(value)):
        return value
    body = value[len(prefix) :].replace("{{", "{").replace("}}", "}")
    return f"{prefix.replace('f', '')}{body}"


def _illegal_split_indices(value: str) -> set[int]:
    r"""Return the indices inside a replacement field or a ``\\N{...}`` escape, where a string must not be split."""
    spans = list(iter_fexpr_spans(value)) if "f" in _string_prefix(value).lower() else []
    unescaped_backslash = False
    characters = iter(enumerate(value))
    for index, char in characters:
        if char == "\\":
            unescaped_backslash = not unescaped_backslash
            continue
        if not unescaped_backslash or char != "N":
            unescaped_backslash = False
            continue
        unescaped_backslash = False
        end = next((end for end, closing in characters if closing == "}"), len(value))
        spans.append((index - 1, end))
    return {index for start, end in spans for index in range(start, end)}


def _break_index(value: str, max_break_idx: int) -> int | None:
    """Return where black splits ``value``: at the last space or East Asian stop up to ``max_break_idx``, else after.

    A piece is never shorter than six characters and never ends in an escaping backslash.
    """
    illegal = _illegal_split_indices(value)

    def allowed(index: int) -> bool:
        escapes = len(value[:index]) - len(value[:index].rstrip("\\"))
        return (
            (value[index] == " " or (index >= 1 and value[index - 1] in _SPLIT_SAFE_CHARS))
            and not escapes % 2
            and len(value) - index >= _MIN_SUBSTR_SIZE
            and index >= _MIN_SUBSTR_SIZE
            and index not in illegal
        )

    index = max_break_idx
    while index - 1 >= 0 and not allowed(index):
        index -= 1
    if allowed(index):
        return index
    index = max_break_idx + 1
    while index + 1 < len(value) and not allowed(index):
        index += 1
    return index if index < len(value) and allowed(index) else None


def _string_paren_wrap_index(line: _Line) -> int | None:
    """Return the string that black wraps in parentheses on its own line, or None (StringParenWrapper's matches).

    The matches in black's order: the string after ``return`` or ``yield``, the string that ends an ``assert`` after a
    comma, the string that ends an assignment or an argument, then a dictionary value and a list element.
    """
    leaves = line.leaves
    if _is_name(leaves[0], "return") or _is_name(leaves[0], "yield"):
        string_idx = 2 if len(leaves) > 1 and _is_empty_par(leaves[1]) else 1
        if string_idx < len(leaves) and leaves[string_idx].type == token.STRING:
            return string_idx
    if _is_name(leaves[0], "assert"):
        for index, leaf in enumerate(leaves[:-1]):
            if leaf.type == token.COMMA and (found := _string_ends_line(leaves, index, comma=False)) is not None:
                return found
    if leaves[0].assign_like:
        for index, leaf in enumerate(leaves):
            if (
                leaf.type in {token.EQUAL, token.PLUSEQUAL}
                and (found := _string_ends_line(leaves, index, comma=leaves[0].assign_like == "argument")) is not None
            ):
                return found
    if leaves[0].dict_entry:
        for index, leaf in enumerate(leaves[:-1]):
            if leaf.type == token.COLON and (found := _string_ends_line(leaves, index, comma=True)) is not None:
                return found
    return 0 if _prefers_paren_wrap(leaves) else None


def _string_ends_line(leaves: Sequence[_Leaf], index: int, *, comma: bool) -> int | None:
    """Return the string after ``leaves[index]`` when it, its trailer and maybe a comma end the line."""
    string_idx = index + 2 if index + 1 < len(leaves) and _is_empty_par(leaves[index + 1]) else index + 1
    if string_idx >= len(leaves) or leaves[string_idx].type != token.STRING:
        return None
    end = _string_trailer_end(leaves, string_idx)
    if comma and end < len(leaves) and leaves[end].type == token.COMMA:
        end += 1
    return string_idx if end >= len(leaves) else None


def _closes_lambda_value(left: Sequence[_Leaf], right: Sequence[_Leaf]) -> bool:
    """Return whether ``right`` ends with the parenthesis around a dictionary value that is a lambda."""
    if not (right and right[-1].type == token.RPAR and (opening := right[-1].opening_bracket) in left[1:-1]):
        return False
    index = left.index(opening)
    return left[index - 1].type == token.COLON and _is_name(left[index + 1], "lambda")


def _wraps_one_expression(
    leaves: Sequence[_Leaf], string_idx: int, right: Sequence[_Leaf], *, old_parens: bool, ends_with_comma: bool
) -> bool:
    """Return whether the parentheses around the string and the leaves ``right`` after it would hold one expression.

    Black wraps whatever follows the string after ``return`` or ``yield``, and changes meaning where that is not one
    expression: ``yield "text", 7,`` becomes ``yield ("text", 7),``, a tuple in a one-element tuple. In
    ``yield "text" if c else d, 7,`` the invisible parentheses around the conditional close before the line ends
    (black fails there).
    """
    if old_parens:
        return bool(right) and right[-1].opening_bracket is leaves[string_idx - 1]
    depth = leaves[string_idx].bracket_depth
    return not ends_with_comma or not any(leaf.type == token.COMMA and leaf.bracket_depth == depth for leaf in right)


def _string_paren_wrap(line: _Line, mode: _Mode, _features: Collection[_Feature]) -> Iterator[_Line]:
    """Put a string that ends an assignment, an argument or a dictionary entry on its own line in parentheses.

    Black's StringParenWrapper: the parentheses replace any around the string, and the leaves after it go inside
    them, except a trailing comma. A line where they would not hold one expression stays unwrapped (see
    ``_wraps_one_expression``).
    """
    leaves = line.leaves
    if leaves[-1].type in OPENING_BRACKETS or (string_idx := _string_paren_wrap_index(line)) is None:
        msg = "This line does not contain any non-atomic strings."
        raise CannotSplitError(msg)
    string_leaf = leaves[string_idx]
    ends_with_comma = leaves[-1].type == token.COMMA
    old_parens = string_idx > 0 and leaves[string_idx - 1].type == token.LPAR
    right = leaves[string_idx + 1 : len(leaves) - ends_with_comma]
    if not _wraps_one_expression(leaves, string_idx, right, old_parens=old_parens, ends_with_comma=ends_with_comma):
        msg = "The parentheses would not hold one expression."
        raise CannotSplitError(msg)
    if (
        not any(char == " " or char in _SPLIT_SAFE_CHARS for char in string_leaf.value)
        and str_width(mode.restore(string_leaf.value)) > mode.line_length - (line.depth + 1) * 4
        and (id(string_leaf.value), string_leaf.value) not in mode.custom_splits
    ):
        msg = "The string would stay too long on its own line."
        raise CannotSplitError(msg)
    _validate_split_string(line, string_idx, mode)
    for leaf in leaves:
        leaf.orphaned = True
    left = leaves[:string_idx]
    lpar = _Leaf(token.LPAR, "(")
    lpar.whitespace = (left.pop() if old_parens else string_leaf).whitespace
    head = line.clone()
    _append_copies(head, left)
    lpar.prefix = lpar.whitespace if head.leaves else ""
    head.append(lpar)
    yield head
    if old_parens or _closes_lambda_value(left, right):
        right = right[:-1]
    body = _Line(
        depth=line.depth + 1,
        inside_brackets=True,
        should_split_rhs=line.should_split_rhs,
        magic_trailing_comma=line.magic_trailing_comma,
    )
    body.append(_new_string(string_leaf.value, string_leaf))
    _append_copies(body, right)
    yield body
    tail = line.clone()
    tail.bracket_tracker = head.bracket_tracker
    tail.append(_Leaf(token.RPAR, ")"))
    if ends_with_comma:
        tail.append(_Leaf(token.COMMA, ","))
    yield tail


def _transform_line(
    line: _Line, mode: _Mode, features: Collection[_Feature] = (), line_str: str = ""
) -> Iterator[_Line]:
    line_str = line_str or str(line)
    transforms: list[Callable[[_Line, _Mode, Collection[_Feature]], Iterator[_Line]]]
    if not line.should_split_rhs and not line.magic_trailing_comma and _is_line_short_enough(line, mode, line_str):
        if not mode.wrap_string_literal:
            yield line
            return
        transforms = [_string_merge, _string_paren_strip]
    elif line.is_def and not _should_split_funcdef_with_rhs(line):
        transforms = [_left_hand_split]
    elif mode.wrap_string_literal:
        transforms = (
            [_string_merge, _string_paren_strip, _string_split, _delimiter_split, _string_paren_wrap, _rhs]
            if line.inside_brackets
            else [_string_merge, _string_paren_strip, _string_split, _string_paren_wrap, _rhs]
        )
    else:
        transforms = [_delimiter_split, _rhs] if line.inside_brackets else [_rhs]
    for transform in transforms:
        try:
            result = _run_transformer(line, transform, mode, features, line_str)
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
    line_str: str = "",
) -> list[_Line]:
    line_str = line_str or str(line)
    result: list[_Line] = []
    for transformed_line in transform(line, mode, features):
        if (transformed_str := str(transformed_line)) == line_str:
            msg = "Line transformer returned an unchanged result"
            raise CannotSplitError(msg)
        result.extend(_transform_line(transformed_line, mode, features, transformed_str))
    if (
        _Feature.FORCE_OPTIONAL_PARENTHESES in features  # noqa: PLR0916
        or transform is not _rhs
        or not line.bracket_tracker.invisible
        or any(bracket.value for bracket in line.bracket_tracker.invisible)
        or _is_line_short_enough(result[0], mode)
        or any(leaf.synthetic or leaf.orphaned for leaf in line.leaves)
    ):
        return result
    line_copy = line.clone()
    _append_copies(line_copy, line.leaves)
    second_opinion = _run_transformer(
        line_copy, transform, mode, {*features, _Feature.FORCE_OPTIONAL_PARENTHESES}, line_str
    )
    if all(_is_line_short_enough(second_line, mode) for second_line in second_opinion):
        return second_opinion
    return result


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
    top_level_seen: list[str] = []
    previous_row = 0
    for item in tokens:
        if (type_ := item.type) in _SKIPPED_TOKENS:
            continue
        if type_ not in _LEAF_TOKENS or (type_ == tokenize.STRING and "\n" in item.string):
            raise _Unsupported
        if leaves and item.start[0] != previous_row and not stack:
            raise _Unsupported
        previous_row = item.end[0]
        if type_ == tokenize.OP:
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
            seen = enclosing[1] if enclosing is not None else top_level_seen
            match item.string:
                case "for":
                    if previous is not None and not _is_name(previous, "async"):
                        leaf.priority_name = COMPREHENSION_PRIORITY
                    seen.append("for")
                case "if" if enclosing is not None and "for" in seen:
                    leaf.priority_name = COMPREHENSION_PRIORITY
                case "if" | "else" if previous is not None:
                    leaf.priority_name = TERNARY_PRIORITY
                case "in":
                    leaf.comparator = not (seen and seen[-1:] == ["for"]) and not (
                        previous is not None and _is_name(previous, "not")
                    )
                    if seen and seen[-1:] == ["for"]:
                        leaf.loop_in = True
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
    if leaves[0].value in _SOFT_KEYWORD_HEADERS and leaves[0].type == token.NAME and leaves[-1].type == token.COLON:
        _read_soft_keyword_header(leaves)
    return leaves, previous_row


_LISTMAKER: Final = frozenset({token.COMMA, "for"})
_DICTSETMAKER: Final = frozenset({token.COMMA, token.COLON, token.DOUBLESTAR, "for"})


def _mark_element_facts(leaves: list[_Leaf]) -> None:  # noqa: PLR0912
    """Mark the facts black's string transformers read from the parents and siblings of a leaf.

    ``assign_like`` names the parent of a name that black may find an assigned string after: ``"argument"`` for a
    keyword argument, ``"expr"`` for the target that starts an assignment and ``"power"`` for the name a call, index
    or attribute starts with. ``wrap_element`` marks a string, or a parenthesized atom, that is a whole element of a
    list, set or tuple display; ``dict_entry`` the first leaf of a dictionary entry.
    """
    closers: dict[int, int] = {}
    contents: dict[int, set[int | str]] = {}
    stack: list[int] = []
    for index, leaf in enumerate(leaves):
        if leaf.type in OPENING_BRACKETS:
            stack.append(index)
            contents[index] = set()
        elif leaf.type in CLOSING_BRACKETS:
            closers[stack.pop()] = index
        elif stack and leaf.type in {token.COMMA, token.COLON, token.DOUBLESTAR}:
            contents[stack[-1]].add(leaf.type)
        elif stack and _is_name(leaf, "for"):
            contents[stack[-1]].add("for")
    starts = {opener + 1 for opener in closers} | {
        index + 1 for index, leaf in enumerate(leaves) if leaf.type == token.COMMA
    }
    stack = []
    for index, leaf in enumerate(leaves):
        opener = stack[-1] if stack else None
        if leaf.type in OPENING_BRACKETS:
            stack.append(index)
        elif leaf.type in CLOSING_BRACKETS:
            stack.pop()
        following = leaves[index + 1] if index + 1 < len(leaves) else None
        if leaf.type == token.NAME and following is not None:
            if (
                following.type == token.EQUAL
                and opener is not None
                and leaves[opener].kind
                in {
                    _Kind.TRAILER,
                    _Kind.CLASSDEF,
                }
            ):
                leaf.assign_like = "argument"
            elif index == 0 and (
                following.type in {token.EQUAL, token.COLON} or following.type in _AUGMENTED_ASSIGNMENTS
            ):
                leaf.assign_like = "expr"
            elif (following.type == token.DOT or following.kind is _Kind.TRAILER) and not (
                index and leaves[index - 1].type == token.DOT
            ):
                leaf.assign_like = "power"
        if (
            opener is None
            or index not in starts
            or leaf.type in CLOSING_BRACKETS
            or leaves[opener].kind is not _Kind.ATOM
            or not contents[opener] & (_DICTSETMAKER if leaves[opener].type == token.LBRACE else _LISTMAKER)
        ):
            continue
        kinds = contents[opener]
        leaf.dict_entry = leaves[opener].type == token.LBRACE and token.COLON in kinds
        end = index
        if leaf.type == token.STRING:
            while end + 1 < len(leaves) and leaves[end + 1].type == token.STRING:
                end += 1
        elif leaf.type == token.LPAR:
            end = closers[index]
        else:
            continue
        leaf.wrap_element = end + 1 < len(leaves) and (
            leaves[end + 1].type == token.COMMA or end + 1 == closers[opener]
        )
    for leaf in leaves:
        if _is_name(leaf, "lambda"):
            leaf.dict_entry = True


_SOFT_KEYWORD_HEADERS: Final = frozenset({"match", "case"})


def _read_soft_keyword_header(leaves: list[_Leaf]) -> None:
    """Read a ``match`` or ``case`` header as black's tree has it: its keyword is no operand of a bracket or sign.

    The guard ``if`` of a ``case``, its first ``if`` outside brackets, is no conditional expression.
    """
    if leaves[1].type in OPENING_BRACKETS:
        leaves[1].kind = _Kind.ATOM
    leaves[1].unary = leaves[1].unary or leaves[1].type in _UNARY_CANDIDATES
    depth = 0
    for leaf in leaves:
        if leaf.type in OPENING_BRACKETS:
            depth += 1
        elif leaf.type in CLOSING_BRACKETS:
            depth -= 1
        elif not depth and _is_name(leaf, "if"):
            leaf.priority_name = 0
            return


def _set_whitespace(leaves: list[_Leaf]) -> None:
    """Give each leaf the whitespace black puts before it.

    In a definition's parameters a default takes spaces around its ``=`` when its parameter is annotated, and the
    commas inside an annotation are marked, as black reads both from its tree.
    """
    stack: list[_Leaf] = []
    annotated = False
    returned = False
    previous: _Leaf | None = None
    for leaf in leaves:
        enclosing = stack[-1] if stack else None
        returned = returned or (leaf.type == token.RARROW and not stack)
        leaf.in_annotation = returned or (annotated and any(item.kind is _Kind.PARAMETERS for item in stack))
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


_PARENS_AFTER: Final = {
    "return": frozenset({"return"}),
    "del": frozenset({"del"}),
    "assert": frozenset({"assert", ","}),
    "if": frozenset({"if"}),
    "elif": frozenset({"elif"}),
    "while": frozenset({"while"}),
    "for": frozenset({"for", "in"}),
    "except": frozenset({"except"}),
    "with": frozenset({"with"}),
    "case": frozenset({"case", "if"}),
}


def _opens_keyword_child(leaf: _Leaf, parens_after: frozenset[str], *, first: bool, loop_in: bool) -> bool:
    """Return whether a span of ``_keyword_children`` starts after ``leaf``, a leaf outside brackets."""
    return (
        first
        or (leaf.type == token.COMMA and "," in parens_after)
        or (loop_in and _is_name(leaf, "in"))
        or (_is_name(leaf, "if") and "case" in parens_after and not leaf.priority_name)
    )


def _ends_keyword_child(leaf: _Leaf, parens_after: frozenset[str], *, last: bool) -> bool:
    """Return whether a span of ``_keyword_children`` ends at ``leaf``: a header's colon or the ``as`` of an except."""
    return (leaf.type == token.COLON and last) or (_is_name(leaf, "as") and "except" in parens_after)


def _keyword_children(leaves: list[_Leaf]) -> list[tuple[int, int]]:
    """Return the spans black wraps in invisible parentheses in a statement led by a keyword (black's ``parens``).

    A span starts after the keyword that leads the statement, the ``in`` of a ``for`` (its first one outside brackets),
    the guard ``if`` of a ``case`` or a comma of an ``assert``, and ends at the next of them, at the ``as`` of an
    ``except``, at the colon that ends a clause header or at the end of the line. The context managers of a ``with``
    statement are one span, and so is a ``case`` pattern. ``except*`` is not laid out, and neither is a ``del`` of
    several targets: black's parentheses around them would delete one tuple instead.
    """
    first = int(_is_name(leaves[0], "async"))
    if (parens_after := _PARENS_AFTER.get(leaves[first].value)) is None or (
        _is_name(leaves[0], "case") and leaves[-1].type != token.COLON
    ):
        return []
    if (_is_name(leaves[first], "except") and len(leaves) > first + 1 and leaves[first + 1].type == token.STAR) or (
        _is_name(leaves[first], "del") and token.COMMA in _top_level_types(leaves)
    ):
        raise _Unsupported
    spans: list[tuple[int, int]] = []
    start: int | None = None
    depth = 0
    last = len(leaves) - 1
    loop_in = "in" in parens_after
    for index, leaf in enumerate(leaves[first:], start=first):
        if leaf.type in OPENING_BRACKETS:
            depth += 1
        elif leaf.type in CLOSING_BRACKETS:
            depth -= 1
        elif depth == 0 and (
            (opens := _opens_keyword_child(leaf, parens_after, first=index == first, loop_in=loop_in))
            or _ends_keyword_child(leaf, parens_after, last=index == last)
        ):
            if start is not None and start < index:
                spans.append((start, index))
            start = index + 1 if opens else None
            loop_in = loop_in and not _is_name(leaf, "in")
    if start is not None and start <= last:
        spans.append((start, last + 1))
    return spans


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
    return leaves[start].type == token.LPAR and leaves[start].kind is _Kind.ATOM and _closes_at_end(leaves, start, end)


def _closes_at_end(leaves: list[_Leaf], start: int, end: int) -> bool:
    """Return whether the bracket that opens the balanced ``leaves[start:end]`` closes at its end."""
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
        elif depth == 0 and leaf.type == token.RARROW:
            spans.append((index + 1, _header_colon(leaves)))
            break
    return spans


def _header_colon(leaves: list[_Leaf]) -> int:
    """Return the index of the colon that ends a header line: its last leaf, or the one before a stub body ``...``."""
    return len(leaves) - 1 if leaves[-1].type == token.COLON else len(leaves) - 2


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


_TERNARY_BOUNDARY_TYPES: Final = frozenset({token.COMMA, token.EQUAL, token.COLON, token.COLONEQUAL, token.RARROW})
_TERNARY_BOUNDARY_NAMES: Final = frozenset({
    "return",
    "yield",
    "else",
    "assert",
    "del",
    "raise",
    "from",
    "if",
    "elif",
    "while",
    "with",
    "as",
    "for",
})


def _ternary_bound(leaf: _Leaf, *, before: bool) -> bool:
    """Return whether ``leaf``, outside brackets, bounds a conditional expression before or after it.

    The ``if`` and ``else`` of nested conditional expressions after it belong to it, and so does a ``for`` loop's
    ``in`` only before it; the ``in`` of a comparison, also of ``not in``, is an operand's operator.
    """
    if leaf.type in _TERNARY_BOUNDARY_TYPES or leaf.type in _AUGMENTED_ASSIGNMENTS:
        return True
    if leaf.type != token.NAME:
        return False
    if before:
        return leaf.value in _TERNARY_BOUNDARY_NAMES or leaf.loop_in
    return leaf.priority_name == COMPREHENSION_PRIORITY or leaf.value in {"as", "from"}


def _ternary_span(leaves: list[_Leaf], index: int) -> tuple[int, int]:
    """Return the span of the conditional expression whose ``if`` is ``leaves[index]``."""
    start, depth = index, 0
    while start > 0:
        leaf = leaves[start - 1]
        if leaf.type in CLOSING_BRACKETS:
            depth += 1
        elif leaf.type in OPENING_BRACKETS:
            if not depth:
                break
            depth -= 1
        elif not depth and (
            _ternary_bound(leaf, before=True)
            or (start == 1 and _is_name(leaf, "case") and leaves[-1].type == token.COLON)
        ):
            break
        start -= 1
    end, depth = index + 1, 0
    while end < len(leaves):
        leaf = leaves[end]
        if leaf.type in OPENING_BRACKETS:
            depth += 1
        elif leaf.type in CLOSING_BRACKETS:
            if not depth:
                break
            depth -= 1
        elif _is_name(leaf, "lambda"):
            raise _Unsupported
        elif not depth and _ternary_bound(leaf, before=False):
            break
        end += 1
    return start, end


def _wrap_conditional_expressions(leaves: list[_Leaf]) -> list[_Leaf]:
    """Wrap each conditional expression in invisible parentheses, as black does, unless parentheses enclose it already.

    A lambda in one is not laid out.
    """
    for if_leaf in [leaf for leaf in reversed(leaves) if leaf.priority_name == TERNARY_PRIORITY and leaf.value == "if"]:
        start, end = _ternary_span(leaves, next(index for index, leaf in enumerate(leaves) if leaf is if_leaf))
        if not (
            start > 0 and end < len(leaves) and leaves[start - 1].type == token.LPAR and leaves[end].type == token.RPAR
        ):
            leaves = _wrap(leaves, start, end)
    return leaves


def _wrap(leaves: list[_Leaf], start: int, end: int) -> list[_Leaf]:
    first = leaves[start]
    lpar = _Leaf(token.LPAR, "")
    lpar.whitespace = lpar.prefix = first.whitespace
    first.whitespace = first.prefix = ""
    rpar = _Leaf(token.RPAR, "")
    return [*leaves[:start], lpar, *leaves[start:end], rpar, *leaves[end:]]


def _dict_value_spans(leaves: list[_Leaf]) -> list[tuple[int, int]]:
    """Return the spans of the values of the dictionary displays in ``leaves``."""
    spans: list[tuple[int, int]] = []
    stack: list[tuple[int, int | None]] = []
    for index, leaf in enumerate(leaves):
        if leaf.type in OPENING_BRACKETS:
            stack.append((index, None))
            continue
        if not stack:
            continue
        opener, value_start = stack[-1]
        ends_value = (
            leaf.type in CLOSING_BRACKETS
            or leaf.type == token.COMMA
            or _is_name(leaf, "for")
            or _is_name(leaf, "async")
        )
        if value_start is not None and ends_value:
            spans.append((value_start, index))
            stack[-1] = (opener, None)
        if leaf.type in CLOSING_BRACKETS:
            stack.pop()
        elif (
            leaf.type == token.COLON
            and value_start is None
            and leaves[opener].type == token.LBRACE
            and leaves[opener].kind is _Kind.ATOM
            and not any(_is_name(item, "lambda") for item in leaves[opener + 1 : index])
        ):
            stack[-1] = (opener, index + 1)
    return spans


def _wrap_dict_values(leaves: list[_Leaf]) -> list[_Leaf]:
    """Put each dictionary value in invisible parentheses, or make its own parentheses invisible.

    Black's preview ``wrap_long_dict_values_in_parens``, which ``--wrap-string-literal`` turns on; a value that is a
    list, set or dictionary display stays as it is.
    """
    before: dict[int, list[_Leaf]] = {}
    after: dict[int, list[_Leaf]] = {}
    for start, end in sorted(_dict_value_spans(leaves), key=lambda span: (span[1], -span[0])):
        first = leaves[start]
        if _is_parenthesized_atom(leaves, start, end):
            _make_parens_invisible(leaves, start, end)
            first.dict_value = not first.value
            continue
        if first.type in OPENING_BRACKETS and first.kind is _Kind.ATOM and _closes_at_end(leaves, start, end):
            continue
        lpar = _Leaf(token.LPAR, "")
        lpar.whitespace = lpar.prefix = first.whitespace
        lpar.dict_value = True
        first.whitespace = first.prefix = ""
        before.setdefault(start, []).append(lpar)
        after.setdefault(end - 1, []).append(_Leaf(token.RPAR, ""))
    if not before:
        return leaves
    result: list[_Leaf] = []
    for index, leaf in enumerate(leaves):
        result.extend(reversed(before.get(index, [])))
        result.append(leaf)
        result.extend(after.get(index, []))
    return result


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
    return takewhile(lambda item: item.type not in _LINE_END_TOKENS, tokens)


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
    Generated modules repeat many lines, so the layouts of lines without lent letters are remembered.
    """
    lay_out = _remembered_layout if restore is str else partial(_logical_line_layout, restore=restore)
    if (
        result := lay_out(
            source, line_length=line_length, wrap_string_literal=wrap_string_literal, assignment=assignment
        )
    ) is None:
        return None
    rows, formatted = result
    return rows, list(formatted)


def _logical_line_layout(
    source: str, *, line_length: int, wrap_string_literal: bool, assignment: bool, restore: Callable[[str], str] = str
) -> tuple[int, tuple[str, ...]] | None:
    """Return what ``format_logical_line`` returns, its new lines as a tuple."""
    lay_out = partial(
        _lay_out_logical_line,
        line_length=line_length,
        wrap_string_literal=wrap_string_literal,
        assignment=assignment,
        restore=restore,
    )
    if (result := lay_out(source)) is None:
        return None
    rows, formatted, same_tokens = result
    if not same_tokens and formatted != source.split("\n")[:rows]:
        formatted = (lay_out("\n".join(formatted)) or result)[1]
    return rows, tuple(formatted)


_remembered_layout = lru_cache(maxsize=1024)(_logical_line_layout)


def _lay_out_logical_line(
    source: str,
    *,
    line_length: int,
    wrap_string_literal: bool,
    assignment: bool,
    restore: Callable[[str], str],
) -> tuple[int, list[str], bool] | None:
    """Lay out one logical line once; also tell whether the new lines hold the same tokens as ``source``.

    The layout reads only the tokens of a line, not where its line breaks were, so laying out new lines with the same
    tokens gives the same lines again.
    """
    indent = len(source) - len(source.lstrip(" "))
    if indent % 4 or source[indent : indent + 1] == "\t":
        return None
    try:
        leaves, rows = _read_leaves(_source_tokens(source[indent:]))
        tokens = [(leaf.type, leaf.value) for leaf in leaves]
        _set_whitespace(leaves)
        if _Line(leaves=leaves).is_def:
            if len(leaves) > 2 and leaves[2].type == token.LSQB:  # noqa: PLR2004
                return None
            leaves = _wrap_spans(leaves, _definition_annotation_spans(leaves))
        elif assignment:
            is_type_statement = _is_name(leaves[0], "type") and len(leaves) > 1 and leaves[1].type == token.NAME
            leaves = _wrap_spans(leaves, [] if is_type_statement else _statement_children(leaves))
        else:
            leaves = _wrap_spans(leaves, _keyword_children(leaves))
        if wrap_string_literal:
            _mark_element_facts(leaves)
            leaves = _wrap_dict_values(leaves)
        leaves = _wrap_conditional_expressions(leaves)
    except (_Unsupported, tokenize.TokenError, SyntaxError):
        return None
    line = _Line(depth=indent // 4)
    for leaf in leaves:
        line.append(leaf)
    lines = list(_transform_line(line, _Mode(line_length, wrap_string_literal, restore)))
    same_tokens = tokens == [(leaf.type, leaf.value) for result in lines for leaf in result.leaves if leaf.value]
    return rows, [str(result) for result in lines], same_tokens
