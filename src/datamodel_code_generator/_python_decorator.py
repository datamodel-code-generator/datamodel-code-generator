"""Target-independent matching for rendered Python decorators."""

from __future__ import annotations

import keyword
import token
import tokenize
from functools import lru_cache
from io import StringIO
from typing import TYPE_CHECKING

from datamodel_code_generator._target_unicode import host_identifiers, is_python_name

if TYPE_CHECKING:
    from collections.abc import Iterator

_OPEN_TO_CLOSE = {"(": ")", "[": "]", "{": "}"}
_CLOSE_TOKENS = frozenset(_OPEN_TO_CLOSE.values())
_INVALID_TOKEN_TYPES = frozenset({token.INDENT, token.DEDENT, token.ERRORTOKEN})
_OPAQUE_STRING_START_TYPES = frozenset(
    token_type for token_type, name in token.tok_name.items() if name in {"FSTRING_START", "TSTRING_START"}
)
_OPAQUE_STRING_END_TYPES = frozenset(
    token_type for token_type, name in token.tok_name.items() if name in {"FSTRING_END", "TSTRING_END"}
)
_COMPONENT_EMPTY = 0
_COMPONENT_HAS_CONTENT = 1
_COMPONENT_AFTER_COMMA = 2
_COMPONENT_AFTER_EQUALS = 3
_PATH_SKIPPED_TOKEN_TYPES = frozenset({token.COMMENT, token.ENDMARKER})
_LINE_BREAK_TOKEN_TYPES = frozenset({token.NEWLINE, tokenize.NL})
_PATH_END_BRACKETS = frozenset("()[]")


def _is_parenthesized_name(body: str, name: str) -> bool:
    """Match a name wrapped only in parentheses without invoking the tokenizer."""
    index = 0
    depth = 0
    body_length = len(body)

    while index < body_length:
        while index < body_length and body[index].isspace():
            index += 1
        if index >= body_length or body[index] != "(":
            break
        depth += 1
        index += 1

    if not depth or not body.startswith(name, index):
        return False
    index += len(name)

    while depth:
        while index < body_length and body[index].isspace():
            index += 1
        if index >= body_length or body[index] != ")":
            return False
        depth -= 1
        index += 1

    return not body[index:].strip()


def _tokenize_opaque_strings(source: str) -> Iterator[tokenize.TokenInfo]:
    """Expose only the decorator shape visible outside string expressions.

    ``tokenize.generate_tokens()`` uses the running Python's tokenizer; it is
    not a parser for the configured target Python syntax. Formatted and
    template string interiors must therefore remain opaque so target-newer
    expressions cannot make decorator matching depend on the runtime version.
    """
    opaque_string_depth = 0
    for item in tokenize.generate_tokens(StringIO(source).readline):
        # START is the expression boundary. Do not expose enclosed tokens to
        # outer-shape validation: interpreting them would apply runtime token
        # structure to target-only syntax and reintroduce version dependence.
        if item.type in _OPAQUE_STRING_START_TYPES:
            if not opaque_string_depth:
                yield item
            opaque_string_depth += 1
        elif opaque_string_depth:
            if item.type in _OPAQUE_STRING_END_TYPES:
                opaque_string_depth -= 1
        else:
            yield item


def _significant_tokens(source: str) -> tuple[tokenize.TokenInfo, ...] | None:
    """Tokenize one logical outer decorator shape with balanced brackets."""
    result: list[tokenize.TokenInfo] = []
    brackets: list[str] = []
    logical_line_ended = False

    try:
        token_iterator = _tokenize_opaque_strings(source)
        while (item := next(token_iterator)).type != token.ENDMARKER:
            if logical_line_ended or item.type in _INVALID_TOKEN_TYPES:
                return None
            if item.type == token.NEWLINE:
                logical_line_ended = True
                continue
            if item.type == tokenize.NL:
                continue
            if item.type == token.COMMENT:
                if not brackets and not result:
                    return None
                continue

            result.append(item)
            if item.type != token.OP:
                continue
            if closing := _OPEN_TO_CLOSE.get(item.string):
                brackets.append(closing)
            elif item.string in _CLOSE_TOKENS and (not brackets or item.string != brackets.pop()):
                return None
    except (SyntaxError, tokenize.TokenError, UnicodeError):
        return None
    return tuple(result)


def _consume_group(tokens: tuple[tokenize.TokenInfo, ...], index: int) -> int | None:
    """Return the index after one balanced group with valid separators."""
    # Each frame stores the state of the current comma-delimited component:
    # 0 is empty, 1 has content, 2 follows a comma, and 3 follows '='. Bracket
    # pairing was already validated while tokenizing.
    stack = [_COMPONENT_EMPTY]
    token_index = index + 1
    while stack:
        current = tokens[token_index]
        if current.type != token.OP:
            stack[-1] = _COMPONENT_HAS_CONTENT
        else:
            value = current.string
            if value in _OPEN_TO_CLOSE:
                stack[-1] = _COMPONENT_HAS_CONTENT
                stack.append(_COMPONENT_EMPTY)
            elif value in _CLOSE_TOKENS:
                if stack.pop() == _COMPONENT_AFTER_EQUALS:
                    return None
            elif value == ",":
                if stack[-1] != _COMPONENT_HAS_CONTENT:
                    return None
                stack[-1] = _COMPONENT_AFTER_COMMA
            elif value == "=":
                if stack[-1] != _COMPONENT_HAS_CONTENT:
                    return None
                stack[-1] = _COMPONENT_AFTER_EQUALS
            elif value == ";":
                return None
            else:
                stack[-1] = _COMPONENT_HAS_CONTENT
        token_index += 1
    return token_index


def _parse_direct_target(
    tokens: tuple[tokenize.TokenInfo, ...],
    index: int,
) -> tuple[str, int, bool] | None:
    """Parse a possibly parenthesized name with at most one direct call."""
    wrapper_depth = 0
    while index < len(tokens) and tokens[index].string == "(":
        wrapper_depth += 1
        index += 1
    if index >= len(tokens) or tokens[index].type != token.NAME or keyword.iskeyword(name := tokens[index].string):
        return None
    index += 1
    has_call = False

    while index < len(tokens):
        if wrapper_depth and tokens[index].string == ")":
            wrapper_depth -= 1
            index += 1
        elif not has_call and tokens[index].string == "(":
            if (group_end := _consume_group(tokens, index)) is None:
                return None
            index = group_end
            has_call = True
        else:
            break
    return None if wrapper_depth else (name, index, has_call)


def python_decorator_path(decorator: str) -> str:
    """Return the path a decorator names: its tokens after ``@`` and opening parentheses, up to its next bracket.

    The tokens after the bracket, such as the arguments of a call, are not read. Comments, line breaks inside the
    opening parentheses and the whitespace around a dot are no part of the path; other whitespace between two tokens
    is kept as one space, so ``a.b if c else d`` names no path. A line break outside the parentheses ends the
    decorator's line; a decorator whose text the tokenizer cannot read before that bracket, or that goes on after that
    line, names itself as a whole, ``@`` included, so no path reads it. A character UTF-8 cannot encode is read as its
    backslash escape, as every Python version reads it the same way, and an identifier character of a target that the
    running interpreter does not know is read as a letter it knows (``host_identifiers``) and given back in the path.
    """
    source = decorator.removeprefix("@").lstrip(" \t\f").encode(errors="backslashreplace").decode()
    lent = host_identifiers(source)
    path: list[str] = []
    previous: tokenize.TokenInfo | None = None
    depth = 0
    line_ended = False
    try:
        for item in tokenize.generate_tokens(StringIO(lent.source if lent else source).readline):
            if item.type in _LINE_BREAK_TOKEN_TYPES:
                line_ended = line_ended or not depth
                continue
            if item.type in _PATH_SKIPPED_TOKEN_TYPES:
                continue
            if line_ended or item.type in _INVALID_TOKEN_TYPES:
                return decorator
            if previous is None and item.string == "(":
                depth += 1
                continue
            if item.type == token.OP and item.string in _PATH_END_BRACKETS:
                break
            if previous is not None and previous.end != item.start and "." not in {previous.string, item.string}:
                path.append(" ")
            path.append(item.string)
            previous = item
    except (SyntaxError, tokenize.TokenError):
        return decorator
    return lent.restore_text("".join(path)) if lent else "".join(path)


@lru_cache(maxsize=128)
def is_named_python_decorator(decorator: str, name: str) -> bool:
    """Match an exact unqualified target without parsing target expressions."""
    if decorator[:1] != "@" or not is_python_name(name, rule="host"):
        return False

    body = decorator[1:].strip()
    if body.isidentifier():
        return body == name
    if _is_parenthesized_name(body, name):
        return True
    if not body or (tokens := _significant_tokens(body)) is None:
        return False
    return (parsed := _parse_direct_target(tokens, 0)) is not None and parsed[1] == len(tokens) and parsed[0] == name
