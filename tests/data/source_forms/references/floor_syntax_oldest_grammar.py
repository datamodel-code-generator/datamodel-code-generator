"""Rule floor-syntax: syntax the oldest supported Python reads is no site, whatever the running Python is.

Pattern matching, ``except*``, parenthesized context managers, positional-only parameters, the walrus operator and
subscripts with ``*`` are all older than the floor.
"""

import contextlib


def match_it(value, /, *, other=None):
    match value:
        case [first, *rest]:
            return first, rest
        case {"key": item}:
            return item
        case _:
            return other


def group():
    try:
        pass
    except* ValueError:
        pass
    with (contextlib.nullcontext() as one, contextlib.nullcontext() as two):
        return one, two


def walrus(items):
    if (found := len(items)) > 1:
        return found
    return items[*items]
