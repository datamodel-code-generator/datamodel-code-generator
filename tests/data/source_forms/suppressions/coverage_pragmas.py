"""#7119: a pragma on a decorator covers the definition, on a clause header its suite, on a continuation line its
statement; a nested exclusion stays inside its suite.
"""

import functools


@functools.cache  # pragma: no cover
def never():
    if never:  # pragma: no cover
        return 1
    return (
        0  # pragma: no cover
    )


def clauses(x):
    try:
        pass
    except ValueError:  # pragma: no cover
        pass
    else :  # pragma: no cover
        x = 1
    finally:
        x = 2
    return x
