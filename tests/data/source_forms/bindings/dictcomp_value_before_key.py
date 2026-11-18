"""Review R33, S5: the compiler visits a dict comprehension's value before its key; two scopes on one line."""


def f(pairs):
    return {tuple(c for c in k): (lambda: v) for k, v in pairs}


def g(rows):
    return {(lambda a: 1): (lambda b: 2) for _ in rows}
