"""#7119: else with a comment, finally, except*, an elif chain, loops with else, and a multi-line header."""


def f(x, items):
    if x:
        pass
    elif not x:
        pass
    else :  # a comment
        pass
    try:
        pass
    except* ValueError:
        pass
    else:
        pass
    finally:
        pass
    for item in items:
        pass
    else:
        pass
    while (
        x
    ):
        break
    with open(x) as handle:
        pass
    try:
        pass
    finally:
        pass
    return handle
