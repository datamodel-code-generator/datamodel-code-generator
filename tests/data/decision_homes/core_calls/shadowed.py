"""Fixture: a name that the scope of the call binds again is not the imported core function."""

from __future__ import annotations

from .core import compute


def parameter(compute, x):
    """Call a parameter."""
    return compute(x, mode=1)


def keyword_only(x, *, compute):
    """Call a keyword-only parameter."""
    return compute(x, mode=1)


def assigned(holder, x):
    """Call a local variable."""
    compute = holder.compute
    return compute(x, mode=1)


def comprehension(functions, x):
    """Call the variable of a comprehension."""
    return [compute(x, mode=1) for compute in functions]


def nested_definition(x):
    """Call a function defined in the function."""

    def compute(y, *, mode):
        return y

    return compute(x, mode=1)


def loop_target(functions, x):
    """Call the target of a loop."""
    for compute in functions:
        compute(x, mode=1)


def context_target(opener, x):
    """Call the target of a with statement."""
    with opener() as compute:
        return compute(x, mode=1)


def exception_target(x):
    """Call the name of an except clause."""
    try:
        return x.missing
    except AttributeError as compute:
        return compute(x, mode=1)


def walrus_target(holder, x):
    """Call the target of an assignment expression."""
    if compute := holder.compute:
        return compute(x, mode=1)
    return None


def enclosing_parameter(compute, x):
    """Call a name that an enclosing function binds."""

    def inner():
        return compute(x, mode=1)

    return inner()


def enclosing_nonlocal(holder):
    """Call a name declared nonlocal."""
    compute = holder.compute

    def inner(x):
        nonlocal compute
        return compute(x, mode=1)

    return inner


def lambda_parameter(x):
    """Call the parameter of a lambda."""
    return (lambda compute: compute(x, mode=1))(x.compute)


def sequence_target(holder, x):
    """Call the capture of a sequence pattern."""
    match holder:
        case [compute]:
            return compute(x, mode=1)
    return None


def star_target(holder, x):
    """Call the star capture of a sequence pattern."""
    match holder:
        case [*compute]:
            return compute(x, mode=1)
    return None


def mapping_rest(holder, x):
    """Call the rest capture of a mapping pattern."""
    match holder:
        case {"mode": 1, **compute}:
            return compute(x, mode=1)
    return None


class Direct:
    """A class attribute is in scope in the class body."""

    compute = None
    value = compute(1, mode=1)
